"""メモリエンジン（抽出・再注入・重複排除・要約）と /memories CRUD の統合テスト。"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from tests.conftest import AppFactory, World, make_settings

pytestmark = pytest.mark.integration

FILLERS = [
    "今日はいい天気だね",
    "お昼ごはん食べた？",
    "最近どう？",
    "眠くなってきた",
    "そっか",
    "なるほどね",
    "へえ、そうなんだ",
    "今なにしてるの？",
    "テレビ見てた",
    "おもしろいね",
]


async def _start(client: httpx.AsyncClient, world: World, headers: dict[str, str]) -> str:
    res = await client.post("/conversations", json={"character_id": str(world.character_id)}, headers=headers)
    assert res.status_code == 200, res.text
    conversation_id: str = res.json()["conversation"]["id"]
    return conversation_id


async def _chat(
    client: httpx.AsyncClient, world: World, conversation_id: str, message: str, headers: dict[str, str]
) -> dict[str, Any]:
    res = await client.post(
        "/chat",
        json={"character_id": str(world.character_id), "conversation_id": conversation_id, "message": message},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body: dict[str, Any] = res.json()
    return body


async def test_memory_recall_after_ten_turns_and_forget_after_delete(client: httpx.AsyncClient, world: World) -> None:
    """A9: 10往復後に以前の話題を振ると記憶を踏まえて返答する / A10: 削除した記憶は使われない。"""
    user = await world.create_user()
    conversation_id = await _start(client, world, user.headers)

    first = await _chat(client, world, conversation_id, "来週、大阪に出張するんだ。ちょっと緊張してる", user.headers)
    assert len(first["memories_created"]) == 1  # 「緊張してる」単体は重要度 < 0.6
    memory_id = first["memories_created"][0]
    row = await world.conn.fetchrow(
        "select content, importance, is_user_edited, source_message_id, embedding is not null as has_embedding"
        " from public.memories where id = $1",
        uuid.UUID(memory_id),
    )
    assert row is not None
    assert row["content"] == "ユーザーは「来週、大阪に出張するんだ」と話していた"
    assert float(row["importance"]) >= 0.6
    assert row["is_user_edited"] is False
    assert str(row["source_message_id"]) == first["user_message"]["id"]
    assert row["has_embedding"]

    for filler in FILLERS:
        body = await _chat(client, world, conversation_id, filler, user.headers)
        assert body["memories_created"] == [], filler

    recall = await _chat(client, world, conversation_id, "大阪のお土産、何がいいと思う？", user.headers)
    assert memory_id in recall["memories_used"]
    assert "出張" in recall["reply"], recall["reply"]

    events = await world.conn.fetch(
        "select payload from public.audit_logs where user_id = $1 and event_type = 'chat.response' order by id desc",
        user.id,
    )
    last_prompt = events[0]["payload"]["prompt_messages"][0]["content"]
    assert "ユーザーは「来週、大阪に出張するんだ」と話していた" in last_prompt

    # A10: 削除すると以後は使われない
    deleted = await client.delete(f"/memories/{memory_id}", headers=user.headers)
    assert deleted.status_code == 204
    after = await _chat(client, world, conversation_id, "大阪のお土産、何がいいと思う？", user.headers)
    assert memory_id not in after["memories_used"]
    assert "出張" not in after["reply"], after["reply"]


async def test_dedupe_updates_auto_memory_and_never_overwrites_user_edited(
    client: httpx.AsyncClient, world: World
) -> None:
    user = await world.create_user()
    conversation_id = await _start(client, world, user.headers)

    # 自動抽出された記憶の近似重複は「更新」扱い（新規作成されない）
    first = await _chat(client, world, conversation_id, "猫が好きなんだ", user.headers)
    assert len(first["memories_created"]) == 1
    again = await _chat(client, world, conversation_id, "猫が好きなんだ", user.headers)
    assert again["memories_created"] == []
    count = await world.conn.fetchval("select count(*) from public.memories where user_id = $1", user.id)
    assert count == 1

    # ユーザーが編集した記憶は上書きされない
    created = await client.post(
        "/memories",
        json={
            "character_id": str(world.character_id),
            "content": "ユーザーは「来週大阪に出張する」と話していた",
            "importance": 0.65,
            "tags": ["secret"],
        },
        headers=user.headers,
    )
    assert created.status_code == 201, created.text
    user_memory = created.json()
    assert user_memory["is_user_edited"] is True
    before = await world.conn.fetchrow(
        "select content, importance, updated_at from public.memories where id = $1", uuid.UUID(user_memory["id"])
    )
    result = await _chat(client, world, conversation_id, "来週大阪に出張する", user.headers)
    assert result["memories_created"] == []
    assert user_memory["id"] in result["memories_used"]
    after = await world.conn.fetchrow(
        "select content, importance, updated_at from public.memories where id = $1", uuid.UUID(user_memory["id"])
    )
    assert after == before
    total = await world.conn.fetchval("select count(*) from public.memories where user_id = $1", user.id)
    assert total == 2
    # secret タグはプロンプトで「二人だけの秘密」として描画される
    prompt = await world.conn.fetchval(
        "select payload->'prompt_messages'->0->>'content' from public.audit_logs"
        " where user_id = $1 and event_type = 'chat.response' order by id desc limit 1",
        user.id,
    )
    assert "（二人だけの秘密）ユーザーは「来週大阪に出張する」と話していた" in prompt


async def test_summarization_triggers_when_threshold_exceeded(app_factory: AppFactory, world: World) -> None:
    # 閾値を下げる: 未要約 > 2×3 = 6 件で要約、短期ウィンドウ = 2×2 = 4 件
    client = await app_factory(make_settings(memory_summary_trigger_turns=3, memory_short_term_turns=2))
    user = await world.create_user()
    conversation_id = await _start(client, world, user.headers)
    conv_uuid = uuid.UUID(conversation_id)
    for i in range(4):
        await world.conn.execute(
            "insert into public.messages (conversation_id, sender_type, body) values ($1, 'user', $2),"
            " ($1, 'character', $3)",
            conv_uuid,
            f"仕事で疲れた話その{i}",
            f"おつかれさま{i}",
        )
    # 既存 1(挨拶) + 8 = 9 件。/chat で +2 = 11 件 > 6 → 要約（短期ウィンドウ 4 件より古い 7 件）
    await _chat(client, world, conversation_id, "今日はいい天気だね", user.headers)

    summaries = await world.conn.fetch(
        "select id, content, importance, tags, source_message_id from public.memories"
        " where user_id = $1 and 'summary' = any(tags)",
        user.id,
    )
    assert len(summaries) == 1
    summary = summaries[0]
    assert float(summary["importance"]) == 0.7
    assert "仕事で疲れた話" in summary["content"]
    cursor = await world.conn.fetchval("select summary_cursor from public.conversations where id = $1", conv_uuid)
    assert cursor is not None
    newer = await world.conn.fetchval(
        "select count(*) from public.messages where conversation_id = $1 and created_at > $2", conv_uuid, cursor
    )
    assert newer == 4  # 短期ウィンドウ分は要約されずに残る
    audit = await world.conn.fetchval(
        "select count(*) from public.audit_logs where user_id = $1 and event_type = 'memory.summary'", user.id
    )
    assert audit == 1

    # 次のチャットでは要約が常に注入される（memories_used に含まれる）
    body = await _chat(client, world, conversation_id, "そっか", user.headers)
    assert str(summary["id"]) in body["memories_used"]
    # 未要約は 6 件（閾値以下）なので2回目の要約は行われない
    count = await world.conn.fetchval(
        "select count(*) from public.memories where user_id = $1 and 'summary' = any(tags)", user.id
    )
    assert count == 1


async def test_memories_crud_and_ownership(client: httpx.AsyncClient, world: World) -> None:
    alice = await world.create_user()
    bob = await world.create_user()
    character_id = str(world.character_id)

    created = await client.post(
        "/memories",
        json={"character_id": character_id, "content": "  犬を飼っている  ", "tags": ["secret", " 家族 ", "secret"]},
        headers=alice.headers,
    )
    assert created.status_code == 201, created.text
    memory = created.json()
    assert memory["content"] == "犬を飼っている"
    assert memory["importance"] == 0.7
    assert memory["tags"] == ["secret", "家族"]
    assert memory["is_user_edited"] is True
    assert memory["source_message_id"] is None
    assert set(memory) == {
        "id",
        "character_id",
        "content",
        "importance",
        "tags",
        "is_user_edited",
        "source_message_id",
        "created_at",
        "updated_at",
    }

    low = await client.post(
        "/memories",
        json={"character_id": character_id, "content": "朝はパン派", "importance": 0.2},
        headers=alice.headers,
    )
    assert low.status_code == 201

    listed = await client.get("/memories", params={"character_id": character_id}, headers=alice.headers)
    assert listed.status_code == 200
    assert [m["content"] for m in listed.json()["memories"]] == ["犬を飼っている", "朝はパン派"]

    # 他人からは見えない・変更できない・削除できない（404）
    assert (await client.get("/memories", params={"character_id": character_id}, headers=bob.headers)).json() == {
        "memories": []
    }
    patch_other = await client.patch(f"/memories/{memory['id']}", json={"importance": 0.1}, headers=bob.headers)
    assert patch_other.status_code == 404
    delete_other = await client.delete(f"/memories/{memory['id']}", headers=bob.headers)
    assert delete_other.status_code == 404

    # 更新（内容変更で再 embedding）
    updated = await client.patch(
        f"/memories/{memory['id']}", json={"content": "柴犬を飼っている", "importance": 0.95}, headers=alice.headers
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["content"] == "柴犬を飼っている"
    assert updated.json()["importance"] == 0.95
    assert updated.json()["tags"] == ["secret", "家族"]
    assert updated.json()["updated_at"] > memory["updated_at"]

    empty_patch = await client.patch(f"/memories/{memory['id']}", json={}, headers=alice.headers)
    assert empty_patch.status_code == 422
    bad_tags = await client.post(
        "/memories", json={"character_id": character_id, "content": "x", "tags": ["t" * 21]}, headers=alice.headers
    )
    assert bad_tags.json()["error"]["message"] == "タグは20文字以内で入力してください"
    bad_json = await client.post(
        "/memories", content=b"{not json", headers={**alice.headers, "Content-Type": "application/json"}
    )
    assert bad_json.status_code == 422
    assert bad_json.json()["error"]["message"] == "リクエストの形式が正しくありません。"

    # バリデーション
    for payload in (
        {"character_id": character_id, "content": ""},
        {"character_id": character_id, "content": "x" * 501},
        {"character_id": character_id, "content": "x", "importance": 1.5},
        {"character_id": character_id, "content": "x", "tags": ["t" * 21]},
        {"character_id": character_id, "content": "x", "tags": [str(i) for i in range(11)]},
    ):
        res = await client.post("/memories", json=payload, headers=alice.headers)
        assert res.status_code == 422, payload
        assert res.json()["error"]["code"] == "validation_error"

    # モデレーション
    blocked = await client.post(
        "/memories", json={"character_id": character_id, "content": "中学生のとき"}, headers=alice.headers
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "moderation_blocked"

    # 存在しないキャラ
    missing = await client.post(
        "/memories", json={"character_id": str(uuid.uuid4()), "content": "x"}, headers=alice.headers
    )
    assert missing.status_code == 404

    # 削除
    assert (await client.delete(f"/memories/{memory['id']}", headers=alice.headers)).status_code == 204
    assert (await client.delete(f"/memories/{memory['id']}", headers=alice.headers)).status_code == 404

    events = await world.conn.fetch("select event_type from public.audit_logs where user_id = $1 order by id", alice.id)
    types = [e["event_type"] for e in events]
    assert types.count("memory.create") == 2
    assert types.count("memory.update") == 1
    assert types.count("memory.delete") == 1
    assert types.count("moderation.flag") == 1
