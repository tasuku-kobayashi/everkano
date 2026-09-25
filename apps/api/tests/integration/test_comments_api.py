"""/comments, /comments/generate の統合テスト。"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.services.llm import LLMError, LLMRequest, LLMResult, MockLLM
from tests.conftest import AppFactory, World, make_settings

pytestmark = pytest.mark.integration


async def _wait_for_reply(world: World, comment_id: uuid.UUID, wait_seconds: float = 3.0) -> dict[str, object] | None:
    deadline = asyncio.get_running_loop().time() + wait_seconds
    while asyncio.get_running_loop().time() < deadline:
        row = await world.conn.fetchrow(
            "select id, author_type, author_character_id, author_user_id, body from public.comments"
            " where parent_comment_id = $1",
            comment_id,
        )
        if row is not None:
            return dict(row)
        await asyncio.sleep(0.05)
    return None


async def test_comment_blocked_by_moderation_is_not_saved(app_factory: AppFactory, world: World) -> None:
    client = await app_factory()
    user = await world.create_user()
    post_id = await world.create_post()
    res = await client.post("/comments", json={"post_id": str(post_id), "body": "死ね"}, headers=user.headers)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "moderation_blocked"
    count = await world.conn.fetchval("select count(*) from public.comments where post_id = $1", post_id)
    assert count == 0
    flag = await world.conn.fetchrow(
        "select payload from public.audit_logs where user_id = $1 and event_type = 'moderation.flag'", user.id
    )
    assert flag is not None
    assert flag["payload"]["context"] == "comment"


async def test_comment_saved_and_character_replies_in_background(app_factory: AppFactory, world: World) -> None:
    client = await app_factory()
    user = await world.create_user()
    post_id = await world.create_post()
    res = await client.post("/comments", json={"post_id": str(post_id), "body": "  かわいい！  "}, headers=user.headers)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["reply_scheduled"] is True
    comment = body["comment"]
    assert comment["body"] == "かわいい！"
    assert comment["author_type"] == "user"
    assert comment["author_user_id"] == str(user.id)
    assert comment["author_character_id"] is None
    assert comment["parent_comment_id"] is None

    reply = await _wait_for_reply(world, uuid.UUID(comment["id"]))
    assert reply is not None
    assert reply["author_type"] == "character"
    assert reply["author_character_id"] == world.character_id
    assert reply["author_user_id"] is None
    assert reply["body"]

    comment_count = await world.conn.fetchval("select comment_count from public.posts where id = $1", post_id)
    assert comment_count == 2
    events = await world.conn.fetch(
        "select event_type, payload from public.audit_logs where user_id = $1 order by id", user.id
    )
    types = [e["event_type"] for e in events]
    assert "comment.create" in types
    generate = next(e for e in events if e["event_type"] == "comment.generate")
    assert generate["payload"]["trigger"] == "auto"


async def test_auto_reply_probability_zero(app_factory: AppFactory, world: World) -> None:
    client = await app_factory(make_settings(comment_auto_reply_probability=0.0))
    user = await world.create_user()
    post_id = await world.create_post()
    res = await client.post("/comments", json={"post_id": str(post_id), "body": "素敵"}, headers=user.headers)
    assert res.status_code == 201
    assert res.json()["reply_scheduled"] is False
    assert await _wait_for_reply(world, uuid.UUID(res.json()["comment"]["id"]), wait_seconds=0.3) is None


async def test_comment_validation_and_visibility(app_factory: AppFactory, world: World) -> None:
    client = await app_factory()
    user = await world.create_user()
    post_id = await world.create_post()
    future_post = await world.create_post(published_at=datetime.now(UTC) + timedelta(days=1))

    too_long = await client.post("/comments", json={"post_id": str(post_id), "body": "あ" * 501}, headers=user.headers)
    assert too_long.status_code == 422
    assert too_long.json()["error"]["message"] == "コメントは500文字以内で入力してください。"
    empty = await client.post("/comments", json={"post_id": str(post_id), "body": " "}, headers=user.headers)
    assert empty.status_code == 422
    unknown = await client.post("/comments", json={"post_id": str(uuid.uuid4()), "body": "hi"}, headers=user.headers)
    assert unknown.status_code == 404
    scheduled = await client.post("/comments", json={"post_id": str(future_post), "body": "hi"}, headers=user.headers)
    assert scheduled.status_code == 404
    bad_parent = await client.post(
        "/comments",
        json={"post_id": str(post_id), "body": "hi", "parent_comment_id": str(uuid.uuid4())},
        headers=user.headers,
    )
    assert bad_parent.status_code == 404


async def test_generate_reply(app_factory: AppFactory, world: World) -> None:
    client = await app_factory(make_settings(comment_auto_reply_probability=0.0))
    user = await world.create_user()
    post_id = await world.create_post()
    created = await client.post(
        "/comments", json={"post_id": str(post_id), "body": "どこのカフェ？"}, headers=user.headers
    )
    comment_id = created.json()["comment"]["id"]

    res = await client.post(
        "/comments/generate", json={"post_id": str(post_id), "parent_comment_id": comment_id}, headers=user.headers
    )
    assert res.status_code == 200, res.text
    reply = res.json()["comment"]
    assert reply["author_type"] == "character"
    assert reply["author_character_id"] == str(world.character_id)
    assert reply["parent_comment_id"] == comment_id
    assert reply["post_id"] == str(post_id)

    # キャラ自身のコメントには返信しない
    self_reply = await client.post(
        "/comments/generate", json={"post_id": str(post_id), "parent_comment_id": reply["id"]}, headers=user.headers
    )
    assert self_reply.status_code == 422

    # 別の投稿のコメントを指定 → 404
    other_post = await world.create_post()
    mismatch = await client.post(
        "/comments/generate", json={"post_id": str(other_post), "parent_comment_id": comment_id}, headers=user.headers
    )
    assert mismatch.status_code == 404


async def test_generate_reply_output_moderated_returns_null(app_factory: AppFactory, world: World) -> None:
    class RudeLLM(MockLLM):
        async def complete(self, request: LLMRequest) -> LLMResult:
            return LLMResult(text="きもいね", model="rude", latency_ms=1)

    client = await app_factory(make_settings(comment_auto_reply_probability=0.0), llm=RudeLLM())
    user = await world.create_user()
    post_id = await world.create_post()
    created = await client.post("/comments", json={"post_id": str(post_id), "body": "いいね"}, headers=user.headers)
    comment_id = created.json()["comment"]["id"]
    res = await client.post(
        "/comments/generate", json={"post_id": str(post_id), "parent_comment_id": comment_id}, headers=user.headers
    )
    assert res.status_code == 200
    assert res.json() == {"comment": None}
    count = await world.conn.fetchval("select count(*) from public.comments where post_id = $1", post_id)
    assert count == 1
    # 差し止めた場合も生成のメタデータ（モデル・プロンプト）は comment.generate に残る
    generate = await world.conn.fetchval(
        "select payload from public.audit_logs where user_id = $1 and event_type = 'comment.generate'", user.id
    )
    assert generate["moderated"] is True
    assert generate["comment_id"] is None
    assert generate["model"] == "rude"
    assert generate["trigger"] == "manual"
    assert generate["prompt_messages"][0]["role"] == "system"


async def test_comment_with_control_characters_is_rejected(app_factory: AppFactory, world: World) -> None:
    client = await app_factory(make_settings(comment_auto_reply_probability=0.0))
    user = await world.create_user()
    post_id = await world.create_post()
    res = await client.post("/comments", json={"post_id": str(post_id), "body": "a\u0000b"}, headers=user.headers)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "validation_error"
    assert res.json()["error"]["message"] == "使用できない文字（制御文字）が含まれています。"
    count = await world.conn.fetchval("select count(*) from public.comments where post_id = $1", post_id)
    assert count == 0


async def test_generate_reply_llm_failure(app_factory: AppFactory, world: World) -> None:
    class FailingLLM(MockLLM):
        async def complete(self, request: LLMRequest) -> LLMResult:
            raise LLMError("down", status_code=500)

    client = await app_factory(make_settings(comment_auto_reply_probability=1.0), llm=FailingLLM())
    user = await world.create_user()
    post_id = await world.create_post()
    # 自動返信（バックグラウンド）が失敗してもコメント投稿自体は成功する
    created = await client.post("/comments", json={"post_id": str(post_id), "body": "いいね"}, headers=user.headers)
    assert created.status_code == 201
    comment_id = created.json()["comment"]["id"]
    res = await client.post(
        "/comments/generate", json={"post_id": str(post_id), "parent_comment_id": comment_id}, headers=user.headers
    )
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "llm_unavailable"
    errors = await world.conn.fetchval(
        "select count(*) from public.audit_logs where user_id = $1 and event_type = 'llm.error'", user.id
    )
    assert errors == 2


async def test_comment_rate_limit(app_factory: AppFactory, world: World) -> None:
    client = await app_factory(make_settings(rate_limit_comments_per_minute=1, comment_auto_reply_probability=0.0))
    user = await world.create_user()
    post_id = await world.create_post()
    assert (
        await client.post("/comments", json={"post_id": str(post_id), "body": "1"}, headers=user.headers)
    ).status_code == 201
    limited = await client.post("/comments", json={"post_id": str(post_id), "body": "2"}, headers=user.headers)
    assert limited.status_code == 429
    assert "retry-after" in limited.headers
