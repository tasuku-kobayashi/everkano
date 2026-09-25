from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import httpx
import pytest

from app.core.logging import AUDIT_LOGGER_NAME, JsonFormatter, configure_logging, request_id_var
from app.main import create_app
from app.services.audit import AuditLogger
from tests.conftest import make_settings


async def test_unhandled_exception_becomes_internal_error_with_request_id() -> None:
    app = create_app(make_settings())

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        res = await client.get("/boom", headers={"X-Request-ID": "trace-1"})
    assert res.status_code == 500
    assert res.headers["x-request-id"] == "trace-1"
    assert res.json() == {
        "error": {
            "code": "internal_error",
            "message": "サーバーでエラーが発生しました。時間をおいて再度お試しください。",
            "request_id": "trace-1",
        }
    }


async def test_invalid_incoming_request_id_is_replaced() -> None:
    app = create_app(make_settings())

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"request_id": request_id_var.get() or ""}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        res = await client.get("/ping", headers={"X-Request-ID": "bad id with spaces"})
    assert res.headers["x-request-id"] != "bad id with spaces"
    assert res.json()["request_id"] == res.headers["x-request-id"]


class _BrokenPool:
    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        raise asyncpg.exceptions.ConnectionDoesNotExistError("db down")
        yield  # pragma: no cover


async def test_audit_failure_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    audit = AuditLogger(_BrokenPool())  # type: ignore[arg-type]
    token = request_id_var.set("req-audit")
    try:
        with caplog.at_level(logging.INFO, logger="everkano.audit"):
            await audit.log("chat.request", user_id=uuid.uuid4(), payload={"message": "hi"})
    finally:
        request_id_var.reset(token)
    records = [r for r in caplog.records if r.name == "everkano.audit"]
    assert [r.levelname for r in records] == ["INFO", "ERROR"]
    mirrored = json.loads(JsonFormatter().format(records[0]))
    assert mirrored["event_type"] == "chat.request"
    assert mirrored["payload"] == {"message": "hi", "request_id": "req-audit"}
    assert mirrored["request_id"] == "req-audit"
    assert "failed to write audit log" in records[1].getMessage()
    # DB 書き込みに失敗した ERROR レコードにも本文が残る
    failed = json.loads(JsonFormatter().format(records[1]))
    assert failed["payload"] == {"message": "hi", "request_id": "req-audit"}


async def test_audit_mirror_survives_warning_log_level(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("WARNING")
    try:
        assert logging.getLogger(AUDIT_LOGGER_NAME).getEffectiveLevel() == logging.INFO
        audit = AuditLogger(_BrokenPool())  # type: ignore[arg-type]
        await audit.log("chat.response", user_id=uuid.uuid4(), payload={"reply": "やっほー"})
        lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
        mirrored = [line for line in lines if line.get("level") == "INFO" and line.get("audit")]
        assert [m["event_type"] for m in mirrored] == ["chat.response"]
        assert mirrored[0]["payload"]["reply"] == "やっほー"
        errors = [line for line in lines if line.get("level") == "ERROR"]
        assert errors[0]["payload"]["reply"] == "やっほー"
        # 一般のロガーは LOG_LEVEL に従う
        assert not logging.getLogger("everkano.chat").isEnabledFor(logging.INFO)
        configure_logging("DEBUG")
        assert logging.getLogger(AUDIT_LOGGER_NAME).getEffectiveLevel() == logging.DEBUG
    finally:
        configure_logging("WARNING")


async def test_client_ip_uses_trusted_header_not_forwarded_for(caplog: pytest.LogCaptureFixture) -> None:
    """X-Forwarded-For の先頭はクライアントが偽装できるため、信頼済みヘッダー（Fly-Client-IP）を記録する。"""
    app = create_app(make_settings(client_ip_header="Fly-Client-IP", log_level="INFO"))

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        with caplog.at_level(logging.INFO, logger="everkano.access"):
            await client.get("/ping", headers={"X-Forwarded-For": "6.6.6.6", "Fly-Client-IP": "203.0.113.7"})
    ips = [getattr(r, "fields", {}).get("client_ip") for r in caplog.records if r.name == "everkano.access"]
    assert ips == ["203.0.113.7"]


async def test_client_ip_falls_back_to_peer_without_trusted_header(caplog: pytest.LogCaptureFixture) -> None:
    app = create_app(make_settings(log_level="INFO"))

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        with caplog.at_level(logging.INFO, logger="everkano.access"):
            await client.get("/ping", headers={"Fly-Client-IP": "203.0.113.7"})
    ips = [getattr(r, "fields", {}).get("client_ip") for r in caplog.records if r.name == "everkano.access"]
    assert len(ips) == 1
    assert ips[0] != "203.0.113.7"
