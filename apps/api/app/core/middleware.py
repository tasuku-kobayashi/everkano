"""リクエストID付与・アクセスログ・未処理例外の 500 変換（純粋な ASGI ミドルウェア）。

- `X-Request-ID` を受け取ればそれを使い（英数と ._- のみ、128文字まで）、無ければ生成する。
- すべてのレスポンスに `X-Request-ID` を付ける（500 を含む）。
- アクセスログは1リクエスト1行の JSON。
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Final

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import DEFAULT_MESSAGES, error_response
from app.core.logging import get_logger, request_id_var

access_logger = get_logger("access")
error_logger = get_logger("errors")

_VALID_REQUEST_ID: Final = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
REQUEST_ID_HEADER: Final[str] = "X-Request-ID"


def _incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == b"x-request-id":
            candidate = bytes(value).decode("latin-1").strip()
            if _VALID_REQUEST_ID.match(candidate):
                return candidate
    return None


def resolve_client_ip(scope: Scope, trusted_header: str | None) -> str | None:
    """ログ用のクライアントIP。

    `trusted_header`（例: Fly.io の `Fly-Client-IP`。エッジが常に上書きするため偽装できない）が
    設定されていればその値を使う。未設定時は接続元アドレス（uvicorn の --proxy-headers が
    FORWARDED_ALLOW_IPS で信頼したプロキシからの X-Forwarded-For のみ反映したもの）。
    X-Forwarded-For の先頭要素はクライアントが自由に付けられるため直接は使わない。
    """
    if trusted_header:
        wanted = trusted_header.lower().encode("latin-1")
        for name, value in scope.get("headers", []):
            if name == wanted:
                raw: bytes = value
                ip = raw.decode("latin-1").strip()
                if ip:
                    return ip[:64]
    client = scope.get("client")
    return str(client[0]) if client else None


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, client_ip_header: str | None = None) -> None:
        self.app = app
        self.client_ip_header = client_ip_header

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        state["client_ip"] = resolve_client_ip(scope, self.client_ip_header)
        started = time.perf_counter()
        status_code = 500
        response_started = False
        logged = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started, logged
            if message["type"] == "http.response.start":
                response_started = True
                status_code = int(message["status"])
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
            elif message["type"] == "http.response.body" and not message.get("more_body", False) and not logged:
                logged = True
                self._log_access(scope, status_code, started)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            error_logger.exception(
                "unhandled exception",
                extra={"fields": {"method": scope.get("method"), "path": scope.get("path")}},
            )
            if not response_started:
                response = error_response(500, "internal_error", DEFAULT_MESSAGES["internal_error"])
                await response(scope, receive, send_wrapper)
            # レスポンス送信後（BackgroundTask 等）の例外はログのみ
        finally:
            request_id_var.reset(token)

    @staticmethod
    def _log_access(scope: Scope, status_code: int, started: float) -> None:
        state = scope.get("state", {})
        headers = dict(scope.get("headers", []))
        access_logger.info(
            "request",
            extra={
                "fields": {
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "client_ip": state.get("client_ip"),
                    "user_id": state.get("user_id"),
                    "user_agent": headers.get(b"user-agent", b"").decode("latin-1")[:200] or None,
                }
            },
        )
