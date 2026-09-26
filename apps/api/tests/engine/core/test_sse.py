"""POST /chat/stream の SSE の形式（packages/shared/src/api.ts の ChatStreamEvent）。"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

from app.engine.pipeline import DeltaEvent, DoneEvent, ErrorEvent, ReplaceEvent
from app.engine.pipeline_sse import HEARTBEAT_COMMENT, OPEN_COMMENT, encode_event, sse_stream
from app.models.dm import ChatResponse, MessageDTO
from app.services.chat import PipelineChannel


def _parse(raw: bytes) -> tuple[str, dict[str, object]]:
    text = raw.decode()
    assert text.endswith("\n\n")
    lines = text.strip("\n").split("\n")
    assert lines[0].startswith("event: ")
    assert lines[1].startswith("data: ")
    assert len(lines) == 2  # data は1行（本文の改行は JSON のエスケープ）
    return lines[0][7:], json.loads(lines[1][6:])


def _response() -> ChatResponse:
    now = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
    conversation = uuid.uuid4()
    user = MessageDTO(
        id=uuid.uuid4(),
        conversation_id=conversation,
        sender_type="user",
        body="hi",
        created_at=now,
        is_proactive=False,
        safety_triggered=False,
    )
    character = MessageDTO(
        id=uuid.uuid4(),
        conversation_id=conversation,
        sender_type="character",
        body="おかえり\n待ってた",
        created_at=now,
        is_proactive=False,
        safety_triggered=False,
    )
    return ChatResponse(
        message_id=character.id,
        reply=character.body,
        memories_used=[],
        memories_created=[],
        user_message=user,
        character_message=character,
        moderated=False,
        safety=None,
    )


def test_event_encoding() -> None:
    assert _parse(encode_event(DeltaEvent("おかえり\n！"))) == ("delta", {"text": "おかえり\n！"})
    assert _parse(encode_event(ReplaceEvent("ごめん", "moderated"))) == (
        "replace",
        {"text": "ごめん", "reason": "moderated"},
    )
    name, data = _parse(encode_event(DoneEvent(_response())))
    assert name == "done"
    assert data["reply"] == "おかえり\n待ってた"
    assert data["safety"] is None
    assert data["memories_created"] == []
    assert data["character_message"]["is_proactive"] is False  # type: ignore[index]
    assert str(data["user_message"]["created_at"]).endswith("Z")  # type: ignore[index]
    name, data = _parse(encode_event(ErrorEvent(503, "llm_unavailable")))
    assert name == "error"
    assert data["code"] == "llm_unavailable"
    assert data["message"] == "ただいま返信できません。しばらくしてから再度お試しください。"
    assert "request_id" in data


async def test_stream_sends_open_comment_heartbeats_and_stops_after_done() -> None:
    channel = PipelineChannel()
    received: list[bytes] = []

    async def consume() -> None:
        async for chunk in sse_stream(channel, heartbeat_seconds=0.05):
            received.append(chunk)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.12)
    channel.put(DeltaEvent("こんにちは。"))
    channel.put(DoneEvent(_response()))
    channel.put(DeltaEvent("done の後は送らない"))
    await asyncio.wait_for(task, timeout=1)
    assert received[0] == OPEN_COMMENT
    assert HEARTBEAT_COMMENT in received
    events = [_parse(c)[0] for c in received if not c.startswith(b":")]
    assert events == ["delta", "done"]


async def test_stream_reports_internal_error_when_producer_crashes() -> None:
    channel = PipelineChannel()
    channel.put(DeltaEvent("途中まで。"))
    channel.close(RuntimeError("boom"))
    chunks = [c async for c in sse_stream(channel, heartbeat_seconds=1)]
    events = [_parse(c) for c in chunks if not c.startswith(b":")]
    assert [e[0] for e in events] == ["delta", "error"]
    assert events[1][1]["code"] == "internal_error"
