"""/health, /conversations, /chat の統合テスト（ローカル Supabase Postgres）。"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from app.services.llm import LLMError, LLMRequest, LLMResult, MockLLM
from tests.conftest import AppFactory, World, make_settings, make_token

pytestmark = pytest.mark.integration


class CountingLLM(MockLLM):
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def complete(self, request: LLMRequest) -> LLMResult:
        self.calls.append(request.purpose)
        return await super().complete(request)


class FailingLLM(MockLLM):
    async def complete(self, request: LLMRequest) -> LLMResult:
        raise LLMError("provider down", status_code=503, retryable=True, attempts=3)


async def _start(client: httpx.AsyncClient, world: World, headers: dict[str, str]) -> dict[str, Any]:
    res = await client.post("/conversations", json={"character_id": str(world.character_id)}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


async def _chat(
    client: httpx.AsyncClient, world: World, conversation_id: str, message: str, headers: dict[str, str]
) -> httpx.Response:
    return await client.post(
        "/chat",
        json={"character_id": str(world.character_id), "conversation_id": conversation_id, "message": message},
        headers=headers,
    )


async def _audit_events(world: World, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await world.conn.fetch(
        "select event_type, payload from public.audit_logs where user_id = $1 order by id", user_id
    )
    return [{"event_type": r["event_type"], "payload": r["payload"]} for r in rows]


# ---------------------------------------------------------------------------


async def test_health(client: httpx.AsyncClient) -> None:
    res = await client.get("/health", headers={"X-Request-ID": "req-abc_123"})
    assert res.status_code == 200
    assert res.json() == {
        "status": "ok",
        "version": "0.1.0",
        "env": "local",
        "llm_mode": "mock",
        "embedding_mode": "hash",
        "db": "ok",
    }
    assert res.headers["x-request-id"] == "req-abc_123"
    generated = await client.get("/health")
    assert len(generated.headers["x-request-id"]) == 32


async def test_auth_required(client: httpx.AsyncClient, world: World) -> None:
    res = await client.post("/conversations", json={"character_id": str(world.character_id)})
    assert res.status_code == 401
    body = res.json()
    assert body["error"]["code"] == "unauthorized"
    assert body["error"]["request_id"] == res.headers["x-request-id"]
    bad = await client.post(
        "/conversations", json={"character_id": str(world.character_id)}, headers={"Authorization": "Bearer nope"}
    )
    assert bad.status_code == 401


async def test_unknown_route_and_cors(client: httpx.AsyncClient) -> None:
    res = await client.get("/nope")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"
    preflight = await client.options(
        "/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:3000"


async def test_create_conversation_saves_greeting_and_is_idempotent(client: httpx.AsyncClient, world: World) -> None:
    user = await world.create_user()
    first = await _start(client, world, user.headers)
    assert first["created"] is True
    assert first["conversation"]["user_id"] == str(user.id)
    greeting = first["greeting_message"]
    assert greeting["sender_type"] == "character"
    assert greeting["body"] == "はじめまして、テスト美咲だよ。これからよろしくね。"

    second = await _start(client, world, user.headers)
    assert second["created"] is False
    assert second["greeting_message"] is None
    assert second["conversation"]["id"] == first["conversation"]["id"]

    count = await world.conn.fetchval(
        "select count(*) from public.messages where conversation_id = $1", uuid.UUID(first["conversation"]["id"])
    )
    assert count == 1
    events = [e["event_type"] for e in await _audit_events(world, user.id)]
    assert events.count("conversation.create") == 1


async def test_conversation_for_unknown_or_inactive_character_is_404(client: httpx.AsyncClient, world: World) -> None:
    user = await world.create_user()
    res = await client.post("/conversations", json={"character_id": str(uuid.uuid4())}, headers=user.headers)
    assert res.status_code == 404
    inactive = await world.create_character(is_active=False)
    res = await client.post("/conversations", json={"character_id": str(inactive)}, headers=user.headers)
    assert res.status_code == 404


async def test_persona_fallback_when_yaml_missing(client: httpx.AsyncClient, world: World) -> None:
    user = await world.create_user()
    character_id = await world.create_character(persona_key="no_such_persona")
    res = await client.post("/conversations", json={"character_id": str(character_id)}, headers=user.headers)
    assert res.status_code == 200
    assert res.json()["greeting_message"]["body"] == "はじめまして、テスト美咲だよ。"
    chat = await client.post(
        "/chat",
        json={
            "character_id": str(character_id),
            "conversation_id": res.json()["conversation"]["id"],
            "message": "こんにちは",
        },
        headers=user.headers,
    )
    assert chat.status_code == 200, chat.text


async def test_chat_happy_path(app_factory: AppFactory, world: World) -> None:
    llm = CountingLLM()
    client = await app_factory(llm=llm)
    user = await world.create_user()
    conversation = (await _start(client, world, user.headers))["conversation"]

    res = await _chat(client, world, conversation["id"], "  今日はいい天気だね  ", user.headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["moderated"] is False
    assert body["reply"]
    assert body["message_id"] == body["character_message"]["id"]
    assert body["user_message"]["body"] == "今日はいい天気だね"  # strip 済み
    assert body["user_message"]["sender_type"] == "user"
    assert body["character_message"]["sender_type"] == "character"
    assert body["user_message"]["created_at"] < body["character_message"]["created_at"]
    assert sorted(llm.calls) == ["chat", "memory_extraction"]

    rows = await world.conn.fetch(
        "select sender_type, body from public.messages where conversation_id = $1 order by created_at",
        uuid.UUID(conversation["id"]),
    )
    assert [r["sender_type"] for r in rows] == ["character", "user", "character"]
    assert rows[2]["body"] == body["reply"]
    last_message_at = await world.conn.fetchval(
        "select last_message_at from public.conversations where id = $1", uuid.UUID(conversation["id"])
    )
    assert last_message_at.isoformat().startswith(body["character_message"]["created_at"][:19])

    events = await _audit_events(world, user.id)
    types = [e["event_type"] for e in events]
    assert "chat.request" in types
    assert "chat.response" in types
    response_event = next(e for e in events if e["event_type"] == "chat.response")
    payload = response_event["payload"]
    assert payload["reply"] == body["reply"]
    assert payload["model"] == "mock-persona-v1"
    assert payload["request_id"] == res.headers["x-request-id"]
    assert payload["prompt_messages"][0]["role"] == "system"
    assert payload["usage"]["total_tokens"] > 0
    request_event = next(e for e in events if e["event_type"] == "chat.request")
    assert request_event["payload"]["message"] == "今日はいい天気だね"


async def test_chat_validation(client: httpx.AsyncClient, world: World) -> None:
    user = await world.create_user()
    conversation = (await _start(client, world, user.headers))["conversation"]
    empty = await _chat(client, world, conversation["id"], "   ", user.headers)
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "validation_error"
    assert empty.json()["error"]["message"] == "メッセージを入力してください。"
    too_long = await _chat(client, world, conversation["id"], "あ" * 2001, user.headers)
    assert too_long.status_code == 422
    assert "2000文字以内" in too_long.json()["error"]["message"]


async def test_chat_ownership(client: httpx.AsyncClient, world: World) -> None:
    alice = await world.create_user()
    bob = await world.create_user()
    conversation = (await _start(client, world, alice.headers))["conversation"]

    # 他人の会話 → 404
    res = await _chat(client, world, conversation["id"], "こんにちは", bob.headers)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"

    # 会話とキャラが一致しない → 404
    other_character = await world.create_character()
    res = await client.post(
        "/chat",
        json={"character_id": str(other_character), "conversation_id": conversation["id"], "message": "こんにちは"},
        headers=alice.headers,
    )
    assert res.status_code == 404

    count = await world.conn.fetchval(
        "select count(*) from public.messages where conversation_id = $1", uuid.UUID(conversation["id"])
    )
    assert count == 1  # 挨拶のみ


async def test_deleted_profile_is_rejected(client: httpx.AsyncClient, world: World) -> None:
    user = await world.create_user()
    await world.conn.execute("update public.profiles set deleted_at = now() where id = $1", user.id)
    res = await client.post("/conversations", json={"character_id": str(world.character_id)}, headers=user.headers)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "account_deleted"
    events = [e["event_type"] for e in await _audit_events(world, user.id)]
    assert "auth.failure" in events


async def test_missing_profile_is_forbidden(client: httpx.AsyncClient, world: World) -> None:
    ghost = uuid.uuid4()
    res = await client.get(
        "/memories",
        params={"character_id": str(world.character_id)},
        headers={"Authorization": f"Bearer {make_token(ghost)}"},
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "forbidden"
    await world.conn.execute("delete from public.audit_logs where user_id = $1", ghost)


async def test_input_moderation_returns_canned_reply_without_llm(app_factory: AppFactory, world: World) -> None:
    llm = CountingLLM()
    client = await app_factory(llm=llm)
    user = await world.create_user()
    conversation = (await _start(client, world, user.headers))["conversation"]

    res = await _chat(client, world, conversation["id"], "中学生のころの話しよう", user.headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["moderated"] is True
    assert body["reply"] == "ごめんね、その話はちょっとできないかな"
    assert body["memories_used"] == []
    assert body["memories_created"] == []
    assert llm.calls == []  # LLM も記憶抽出も呼ばれない

    count = await world.conn.fetchval(
        "select count(*) from public.messages where conversation_id = $1", uuid.UUID(conversation["id"])
    )
    assert count == 3
    events = await _audit_events(world, user.id)
    flag = next(e for e in events if e["event_type"] == "moderation.flag")
    assert flag["payload"]["stage"] == "input"
    assert "minor" in flag["payload"]["categories"]
    assert flag["payload"]["text"] == "中学生のころの話しよう"
    response = next(e for e in events if e["event_type"] == "chat.response")
    assert response["payload"]["moderated"] is True


async def test_output_moderation_replaces_reply(app_factory: AppFactory, world: World) -> None:
    class RudeLLM(MockLLM):
        async def complete(self, request: LLMRequest) -> LLMResult:
            if request.purpose == "chat":
                return LLMResult(text="そんなのうざいよ", model="rude", latency_ms=1)
            return await super().complete(request)

    client = await app_factory(llm=RudeLLM())
    user = await world.create_user()
    conversation = (await _start(client, world, user.headers))["conversation"]
    res = await _chat(client, world, conversation["id"], "ねえ聞いて", user.headers)
    assert res.status_code == 200
    body = res.json()
    assert body["moderated"] is True
    assert body["reply"] == "ごめんね、その話はちょっとできないかな"
    events = await _audit_events(world, user.id)
    flag = next(e for e in events if e["event_type"] == "moderation.flag")
    assert flag["payload"]["stage"] == "output"
    assert flag["payload"]["categories"] == ["persona_ng_word"]
    assert flag["payload"]["text"] == "そんなのうざいよ"


async def test_llm_failure_returns_503_and_saves_nothing(app_factory: AppFactory, world: World) -> None:
    client = await app_factory(llm=FailingLLM())
    user = await world.create_user()
    conversation = (await _start(client, world, user.headers))["conversation"]
    res = await _chat(client, world, conversation["id"], "来週、大阪に出張するんだ", user.headers)
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "llm_unavailable"
    count = await world.conn.fetchval(
        "select count(*) from public.messages where conversation_id = $1", uuid.UUID(conversation["id"])
    )
    assert count == 1  # 挨拶のみ
    memories = await world.conn.fetchval("select count(*) from public.memories where user_id = $1", user.id)
    assert memories == 0
    events = [e["event_type"] for e in await _audit_events(world, user.id)]
    assert "llm.error" in events
    assert "chat.response" not in events


async def test_rate_limit(app_factory: AppFactory, world: World) -> None:
    client = await app_factory(make_settings(rate_limit_chat_per_minute=2))
    user = await world.create_user()
    conversation = (await _start(client, world, user.headers))["conversation"]
    for i in range(2):
        ok = await _chat(client, world, conversation["id"], f"こんにちは{i}", user.headers)
        assert ok.status_code == 200
    limited = await _chat(client, world, conversation["id"], "こんにちは3", user.headers)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(limited.headers["retry-after"]) <= 60
    # 他のユーザーは影響を受けない
    other = await world.create_user()
    conversation2 = (await _start(client, world, other.headers))["conversation"]
    assert (await _chat(client, world, conversation2["id"], "やあ", other.headers)).status_code == 200
