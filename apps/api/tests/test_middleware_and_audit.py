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

from app.core.logging import JsonFormatter, request_id_var
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
