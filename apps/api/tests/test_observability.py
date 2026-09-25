"""Sentry に送る内容のスクラブ（トークン・DM 本文・ローカル変数・監査ログを送らない）。"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import sentry_sdk
from fastapi import Header
from pydantic import BaseModel
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from app import main as app_main
from app.core.logging import AUDIT_LOGGER_NAME
from app.core.observability import REDACTED, init_sentry, scrub_breadcrumb, scrub_event
from tests.conftest import make_settings

TOKEN = "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9.secret-payload.secret-signature"
SECRET_TEXT = "実は先月から心療内科に通ってるんだ、秘密ね"
DSN = "https://public@o0.ingest.sentry.io/0"


class _Capture:
    def __init__(self) -> None:
        self.envelopes: list[str] = []
        self.events: list[dict[str, Any]] = []

    def transport(self) -> type[Transport]:
        capture = self

        class CapturingTransport(Transport):
            def capture_envelope(self, envelope: Envelope) -> None:
                capture.envelopes.append(envelope.serialize().decode("utf-8", errors="replace"))
                event = envelope.get_event()
                if event is not None:
                    capture.events.append(dict(event))

        return CapturingTransport


@pytest.fixture
def sentry_capture() -> Iterator[_Capture]:
    capture = _Capture()
    try:
        yield capture
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.init()  # DSN なし（何も送らない）クライアントに戻す


class _Body(BaseModel):
    message: str


async def test_unhandled_error_event_contains_no_token_body_or_audit_payload(
    sentry_capture: _Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # create_app が init_sentry を使っていること（= 本番と同じ設定）を、送信先だけ差し替えて確認する
    monkeypatch.setattr(
        app_main, "init_sentry", lambda settings: init_sentry(settings, transport=sentry_capture.transport())
    )
    app = app_main.create_app(make_settings(sentry_dsn=DSN))

    @app.post("/boom")
    async def boom(body: _Body, authorization: str = Header()) -> None:
        local_token = authorization  # noqa: F841 - ローカル変数に残っても送られないことを確認する
        # 監査ログ（stdout への複製）はパンくず・イベントにならないこと
        logging.getLogger(AUDIT_LOGGER_NAME).info(
            "chat.request", extra={"fields": {"payload": {"message": body.message}}}
        )
        logging.getLogger(AUDIT_LOGGER_NAME).error(
            "failed to write audit log to database", extra={"fields": {"payload": {"message": body.message}}}
        )
        logging.getLogger("everkano.chat").info("processing %s", body.message)
        raise RuntimeError("boom")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        res = await client.post("/boom", json={"message": SECRET_TEXT}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert res.status_code == 500
    sentry_sdk.flush()

    assert sentry_capture.envelopes, "the unhandled exception must still be reported to Sentry"
    sent = "\n".join(sentry_capture.envelopes)
    assert "unhandled exception" in sent
    # ソースコードの行（source context）は送られるので "Bearer" の文字列自体ではなくトークンの値で確認する
    assert TOKEN not in sent
    assert "secret-payload" not in sent
    assert "secret-signature" not in sent
    assert SECRET_TEXT not in sent
    # 監査ロガーはイベントにもパンくずにもならない（ERROR の「監査ログ書き込み失敗」も payload ごと送らない）
    assert [e.get("logger") for e in sentry_capture.events] == ["everkano.errors"]
    for event in sentry_capture.events:
        crumbs = (event.get("breadcrumbs") or {}).get("values") or []
        assert all(not str(c.get("category", "")).startswith("everkano") for c in crumbs)
        assert "data" not in (event.get("request") or {})
        for value in (event.get("exception") or {}).get("values") or []:
            for frame in (value.get("stacktrace") or {}).get("frames") or []:
                assert "vars" not in frame


def test_scrub_event_removes_request_body_headers_vars_and_payloads() -> None:
    event: dict[str, Any] = {
        "request": {
            "method": "POST",
            "url": "http://api/chat",
            "data": {"message": SECRET_TEXT},
            "cookies": {"sb": "x"},
            "headers": {"Authorization": f"Bearer {TOKEN}", "User-Agent": "iPhone", "Cookie": "a=b"},
        },
        "exception": {
            "values": [{"stacktrace": {"frames": [{"function": "f", "vars": {"scope": {"headers": [TOKEN]}}}]}}]
        },
        "extra": {"fields": {"payload": {"message": SECRET_TEXT}, "path": "/chat", "error": SECRET_TEXT}},
        "logentry": {"message": "processing %s", "params": [SECRET_TEXT]},
        "breadcrumbs": {
            "values": [
                {"category": "everkano.audit", "message": SECRET_TEXT},
                {"category": "httplib", "data": {"url": "https://llm/chat/completions", "method": "POST"}},
            ]
        },
        "user": {"id": "u", "ip_address": "1.2.3.4"},
    }
    scrubbed = scrub_event(event)
    assert "data" not in scrubbed["request"]
    assert "cookies" not in scrubbed["request"]
    assert scrubbed["request"]["headers"] == {"User-Agent": "iPhone"}
    assert "vars" not in scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert scrubbed["extra"]["fields"]["payload"] == REDACTED
    assert scrubbed["extra"]["fields"]["error"] == REDACTED
    assert scrubbed["extra"]["fields"]["path"] == "/chat"
    assert "params" not in scrubbed["logentry"]
    assert [b["category"] for b in scrubbed["breadcrumbs"]["values"]] == ["httplib"]
    assert "user" not in scrubbed
    assert SECRET_TEXT not in repr(scrubbed)
    assert TOKEN not in repr(scrubbed)


def test_scrub_breadcrumb_drops_log_breadcrumbs() -> None:
    assert scrub_breadcrumb({"category": "everkano.chat", "message": SECRET_TEXT}) is None
    kept = scrub_breadcrumb({"category": "http", "data": {"url": "https://x", "body": SECRET_TEXT}})
    assert kept is not None
    assert kept["data"] == {"url": "https://x", "body": REDACTED}


def test_init_sentry_is_noop_without_dsn() -> None:
    assert init_sentry(make_settings()) is False
