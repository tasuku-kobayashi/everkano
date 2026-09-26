"""`POST /chat/stream` の Server-Sent Events 変換（packages/shared/src/api.ts の ChatStreamEvent）。

- 各イベントは `event: <type>` + `data: <JSON>` + 空行。JSON は1行（改行はエスケープされる）。
- 開始直後にコメント行（`: ok`）を送り、プロキシ・ブラウザにヘッダーを早く届ける。
- イベントが無い間は CHAT_STREAM_HEARTBEAT_SECONDS ごとにコメント行（`: keep-alive`）を送る（中継の
  アイドルタイムアウトで切られないように）。
- バッファリングさせないヘッダー（Cache-Control: no-cache, no-transform / X-Accel-Buffering: no）を付ける。
- 想定外の例外で生成が止まった場合は `error`（internal_error）を送って閉じる（詳細はサーバーのログ）。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, Final

from app.core.errors import DEFAULT_MESSAGES
from app.core.logging import request_id_var
from app.engine.pipeline import DeltaEvent, DoneEvent, ErrorEvent, PipelineEvent, ReplaceEvent
from app.services.chat import PipelineChannel

SSE_MEDIA_TYPE: Final[str] = "text/event-stream"
SSE_HEADERS: Final[dict[str, str]] = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}
OPEN_COMMENT: Final[bytes] = b": ok\n\n"
HEARTBEAT_COMMENT: Final[bytes] = b": keep-alive\n\n"


def format_sse(event: str, data: Any) -> bytes:
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {body}\n\n".encode()


def encode_event(event: PipelineEvent) -> bytes:
    if isinstance(event, DeltaEvent):
        return format_sse("delta", {"text": event.text})
    if isinstance(event, ReplaceEvent):
        return format_sse("replace", {"text": event.text, "reason": event.reason})
    if isinstance(event, DoneEvent):
        return format_sse("done", event.response.model_dump(mode="json"))
    return format_sse("error", error_data(event))


def error_data(event: ErrorEvent) -> dict[str, Any]:
    return {
        "code": event.code,
        "message": event.message or DEFAULT_MESSAGES[event.code],
        "request_id": request_id_var.get(),
    }


async def sse_stream(channel: PipelineChannel, *, heartbeat_seconds: float) -> AsyncIterator[bytes]:
    yield OPEN_COMMENT
    while True:
        try:
            event = await asyncio.wait_for(channel.next(), timeout=heartbeat_seconds)
        except TimeoutError:
            yield HEARTBEAT_COMMENT
            continue
        if event is None:
            if channel.error is not None and not isinstance(channel.error, asyncio.CancelledError):
                yield format_sse("error", error_data(ErrorEvent(500, "internal_error")))
            return
        yield encode_event(event)
        if isinstance(event, DoneEvent | ErrorEvent):
            return
