"""リクエスト本文の上限（BodySizeLimitMiddleware）。認証より前・本文を読む前に 413 になること。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from starlette.types import Receive, Scope, Send

from app.core.middleware import BodySizeLimitMiddleware
from app.main import create_app
from tests.conftest import make_settings

LIMIT = 64 * 1024
ORIGIN = "http://localhost:3000"


def _app() -> FastAPI:
    # lifespan を起動しない（= DB もサービスも無い）状態でも 413 はルーターに届く前に返ること
    app = create_app(make_settings(max_request_body_bytes=LIMIT))

    @app.post("/echo")
    async def echo(body: dict[str, Any]) -> dict[str, int]:
        return {"size": len(json.dumps(body))}

    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _assert_413(res: httpx.Response) -> None:
    assert res.status_code == 413, res.text
    body = res.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"] == "送信内容が大きすぎます。内容を短くして再度お試しください。"
    assert body["error"]["request_id"] == res.headers["x-request-id"]


async def test_oversized_body_without_auth_is_rejected_before_routing() -> None:
    """1MB の本文 + Authorization なし → 401 ではなく 413（本文を読まない・ルーターに届かない）。"""
    payload = json.dumps({"character_id": "x", "conversation_id": "y", "message": "あ" * 400_000})
    async with _client(_app()) as client:
        res = await client.post(
            "/chat", content=payload, headers={"Content-Type": "application/json", "Origin": ORIGIN}
        )
    _assert_413(res)
    # CORS ヘッダーも付く（ブラウザでエラー本文を読める）
    assert res.headers["access-control-allow-origin"] == ORIGIN


async def test_oversized_chunked_body_is_rejected() -> None:
    """Content-Length の無い（chunked）本文も、受信量が上限を超えた時点で 413。"""

    async def chunks() -> AsyncIterator[bytes]:
        yield b'{"message": "'
        for _ in range(64):
            yield b"a" * 4096
        yield b'"}'

    async with _client(_app()) as client:
        res = await client.post("/chat", content=chunks(), headers={"Content-Type": "application/json"})
    assert "content-length" not in res.request.headers
    _assert_413(res)


@pytest.mark.parametrize("size", [LIMIT - 100, 8 * 1024])
async def test_bodies_under_the_limit_pass(size: int) -> None:
    payload = json.dumps({"text": "a" * (size - 20)})
    assert len(payload.encode()) <= LIMIT
    async with _client(_app()) as client:
        res = await client.post("/echo", content=payload, headers={"Content-Type": "application/json"})
    assert res.status_code == 200, res.text


async def test_max_chat_message_fits_default_limit() -> None:
    """正規の最大（2000 文字・4 バイト文字でも）が既定の上限に十分収まること。"""
    settings = make_settings()
    payload = json.dumps(
        {"character_id": "0" * 36, "conversation_id": "0" * 36, "message": "😀" * 2000}, ensure_ascii=True
    )
    assert len(payload.encode()) * 2 < settings.max_request_body_bytes


async def test_chunked_body_under_the_limit_passes() -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b'{"text": "'
        yield b"a" * 1000
        yield b'"}'

    async with _client(_app()) as client:
        res = await client.post("/echo", content=chunks(), headers={"Content-Type": "application/json"})
    assert res.status_code == 200, res.text


async def test_oversized_stream_read_outside_fastapi_routes_is_still_413() -> None:
    """ルートの例外ハンドラを通らない場所で本文を読んだ場合も 500 ではなく 413。"""

    async def raw_app(scope: Scope, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(20):
            yield b"a" * 1024

    app = BodySizeLimitMiddleware(raw_app, max_bytes=4096)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        res = await client.post("/", content=chunks())
    assert res.status_code == 413
    assert res.json()["error"]["code"] == "validation_error"
