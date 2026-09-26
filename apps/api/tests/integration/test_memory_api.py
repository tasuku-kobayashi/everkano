"""メモリエンジンの端から端まで（/chat → 返答の後のジョブ post_turn・memory.summarize → 次の /chat の想起）と
/memories CRUD の統合テスト。

エンジン v1.0 では、記憶の抽出（memory_analysis）と要約は返答の後のジョブで行う（E8）。/chat の memories_created は
常に空で、テストはジョブのワーカーを `run_until_idle` で決定的に動かす（この会話のジョブだけ）。
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from app.container import Services
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


def _services(client: httpx.AsyncClient) -> Services:
    services: Services = client.app.state.services  # type: ignore[attr-defined]
    return services


async def _drain(client: httpx.AsyncClient, conversation_id: str) -> int:
    """この会話の返答の後のジョブ（post_turn・memory.summarize）を今すぐ実行する。"""
    worker = _services(client).engine.worker
    return await worker.run_until_idle(ignore_run_at=True, dedupe_keys=[conversation_id])


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
    """A9: 10往復後に以前の話題を振ると記憶を踏まえて返答する / A10: 削除した記憶は使われず、作り直されない。"""
    user = await world.create_user()
    conversation_id = await _start(client, world, user.headers)

    first = await _chat(client, world, conversation_id, "来週、大阪に出張するんだ。ちょっと緊張してる", user.headers)
    assert first["memories_created"] == []  # 抽出は返答の後のジョブ（E8）
    assert await _drain(client, conversation_id) >= 1
    rows = await world.conn.fetch(
        "select id, kind, content, is_user_edited, source_message_id, embedding is not null as has_embedding"
        " from public.memories where user_id = $1 order by created_at, id",
        user.id,
    )
    promise_memory = next(r for r in rows if r["kind"] == "promise")
    assert "大阪に出張する" in promise_memory["content"]
    assert promise_memory["is_user_edited"] is False
    assert str(promise_memory["source_message_id"]) == first["user_message"]["id"]
    assert promise_memory["has_embedding"]
    assert any(r["kind"] == "emotion" for r in rows)  # 「緊張してる」（予定について）
    promise = await world.conn.fetchrow("select * from public.promises where user_id = $1", user.id)
    assert promise["due_precision"] == "week"

    for filler in FILLERS:
        body = await _chat(client, world, conversation_id, filler, user.headers)
        assert body["memories_created"] == [], filler
    await _drain(client, conversation_id)

    memory_id = str(promise_memory["id"])
    for question in ("大阪のお土産、何がいいと思う？", "大阪でおすすめの場所ある？"):  # 後者は Web の E2E（A9）と同じ
        recall = await _chat(client, world, conversation_id, question, user.headers)
        assert memory_id in recall["memories_used"], question
        assert "出張" in recall["reply"], recall["reply"]

    # A10: 削除すると以後は使われず、同じ話をしても自動では作り直さない（E5: 墓標）
    for row in rows:
        deleted = await client.delete(f"/memories/{row['id']}", headers=user.headers)
        assert deleted.status_code == 204
    after = await _chat(client, world, conversation_id, "大阪のお土産、何がいいと思う？", user.headers)
    assert memory_id not in after["memories_used"]
    assert "出張" not in after["reply"], after["reply"]
    await _chat(client, world, conversation_id, "来週、大阪に出張するんだ。ちょっと緊張してる", user.headers)
    await _drain(client, conversation_id)
    remaining = await world.conn.fetchval("select count(*) from public.memories where user_id = $1", user.id)
    assert remaining == 0


async def test_dedupe_and_user_edited_memories_are_never_overwritten(client: httpx.AsyncClient, world: World) -> None:
    user = await world.create_user()
    conversation_id = await _start(client, world, user.headers)

    # 同じ内容の記憶は増えない（M3）
    await _chat(client, world, conversation_id, "猫が好きなんだ", user.headers)
    await _drain(client, conversation_id)
    await _chat(client, world, conversation_id, "猫が好きなんだ", user.headers)
    await _drain(client, conversation_id)
    count = await world.conn.fetchval("select count(*) from public.memories where user_id = $1", user.id)
    assert count == 1

    # ユーザーが書いた記憶は自動処理で上書きされない（E5）
    created = await client.post(
        "/memories",
        json={
            "character_id": str(world.character_id),
            "content": "広告代理店で働いてる",
            "importance": 0.8,
            "tags": ["secret"],
        },
        headers=user.headers,
    )
    assert created.status_code == 201, created.text
    user_memory = created.json()
    assert user_memory["is_user_edited"] is True
    before = await world.conn.fetchrow("select * from public.memories where id = $1", uuid.UUID(user_memory["id"]))
    result = await _chat(client, world, conversation_id, "転職して、今は銀行で働いてるよ", user.headers)
    assert result["memories_created"] == []
    await _drain(client, conversation_id)
    after = await world.conn.fetchrow("select * from public.memories where id = $1", uuid.UUID(user_memory["id"]))
    # 返答に使われた記録（参照回数）以外は何も変わらない
    reference_columns = {"last_referenced_at", "reference_count"}
    assert {k: v for k, v in dict(after).items() if k not in reference_columns} == {
        k: v for k, v in dict(before).items() if k not in reference_columns
    }
    contents = [
        r["content"]
        for r in await world.conn.fetch(
            "select content from public.memories where user_id = $1 and status = 'active'", user.id
        )
    ]
    assert any("銀行" in c for c in contents)  # 新しい情報は別の記憶として残る
    skipped = await world.conn.fetchval(
        "select count(*) from public.audit_logs where user_id = $1 and event_type = 'memory.user_edited_skipped'",
        user.id,
    )
    assert skipped == 1


async def test_summarization_runs_in_the_background_job(app_factory: AppFactory, world: World) -> None:
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
    await _drain(client, conversation_id)

    summaries = await world.conn.fetch(
        "select id, kind, content, importance, tags from public.memories where user_id = $1 and kind = 'summary'",
        user.id,
    )
    assert len(summaries) == 1
    summary = summaries[0]
    assert float(summary["importance"]) == 0.7
    assert list(summary["tags"]) == ["summary"]
    assert "仕事で疲れた話" in summary["content"]
    cursor = await world.conn.fetchval("select summary_cursor from public.conversations where id = $1", conv_uuid)
    newer = await world.conn.fetchval(
        "select count(*) from public.messages where conversation_id = $1 and created_at > $2", conv_uuid, cursor
    )
    assert newer == 4  # 短期ウィンドウ分は要約されずに残る
    audit = await world.conn.fetchval(
        "select count(*) from public.audit_logs where user_id = $1 and event_type = 'memory.summary'", user.id
    )
    assert audit == 1

    # 次のチャットでは最新の要約が常に注入される
    body = await _chat(client, world, conversation_id, "そっか", user.headers)
    assert str(summary["id"]) in body["memories_used"]


async def test_summary_excludes_moderated_turns(app_factory: AppFactory, world: World) -> None:
    client = await app_factory(make_settings(memory_summary_trigger_turns=3, memory_short_term_turns=2))
    user = await world.create_user()
    conversation_id = await _start(client, world, user.headers)
    conv_uuid = uuid.UUID(conversation_id)
    turns = [
        ("仕事で疲れた話その0", "おつかれさま0"),
        ("高校生の子が好きなんだ", "ごめんね、その話はちょっとできないかな"),  # Gate #1 で差し止められたターン
        ("仕事で疲れた話その1", "おつかれさま1"),
        ("仕事で疲れた話その2", "おつかれさま2"),
    ]
    for user_body, character_body in turns:
        await world.conn.execute(
            "insert into public.messages (conversation_id, sender_type, body) values ($1, 'user', $2),"
            " ($1, 'character', $3)",
            conv_uuid,
            user_body,
            character_body,
        )
    await _chat(client, world, conversation_id, "今日はいい天気だね", user.headers)
    await _drain(client, conversation_id)

    summary = await world.conn.fetchval(
        "select content from public.memories where user_id = $1 and kind = 'summary'", user.id
    )
    assert summary is not None
    assert "仕事で疲れた話" in summary
    assert "高校生" not in summary
    audit = await world.conn.fetchval(
        "select payload from public.audit_logs where user_id = $1 and event_type = 'memory.summary'", user.id
    )
    assert audit["excluded_moderated_messages"] == 2
    cursor = await world.conn.fetchval("select summary_cursor from public.conversations where id = $1", conv_uuid)
    newer = await world.conn.fetchval(
        "select count(*) from public.messages where conversation_id = $1 and created_at > $2", conv_uuid, cursor
    )
    assert newer == 4


async def test_memory_with_control_characters_is_rejected(client: httpx.AsyncClient, world: World) -> None:
    user = await world.create_user()
    for payload in (
        {"character_id": str(world.character_id), "content": "a\u0000b"},
        {"character_id": str(world.character_id), "content": "ok", "tags": ["x\u0000"]},
    ):
        res = await client.post("/memories", json=payload, headers=user.headers)
        assert res.status_code == 422, res.text
        assert res.json()["error"]["code"] == "validation_error"
    count = await world.conn.fetchval("select count(*) from public.memories where user_id = $1", user.id)
    assert count == 0


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
    assert memory["kind"] == "fact"
    assert memory["status"] == "active"
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
        "kind",
        "status",
        "superseded_by",
        "superseded_at",
        "last_referenced_at",
        "reference_count",
    }

    low = await client.post(
        "/memories",
        json={"character_id": character_id, "content": "朝はパン派", "importance": 0.2, "kind": "preference"},
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
        {"character_id": character_id, "content": "x", "kind": "summary"},
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

    # 削除（墓標を残す）
    assert (await client.delete(f"/memories/{memory['id']}", headers=alice.headers)).status_code == 204
    assert (await client.delete(f"/memories/{memory['id']}", headers=alice.headers)).status_code == 404
    tombstones = await world.conn.fetchval("select count(*) from public.memory_tombstones where user_id = $1", alice.id)
    assert tombstones == 1

    events = await world.conn.fetch("select event_type from public.audit_logs where user_id = $1 order by id", alice.id)
    types = [e["event_type"] for e in events]
    assert types.count("memory.create") == 2
    assert types.count("memory.update") == 1
    assert types.count("memory.delete") == 1
    assert types.count("moderation.flag") == 1
