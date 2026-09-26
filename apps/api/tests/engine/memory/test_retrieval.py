"""retrieve_context / mark_referenced / due_promises / mark_promise_mentioned の統合テスト。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.db import vector_literal
from app.engine.memory.embedding import HashEmbedding
from app.engine.types import JST
from tests.conftest import World
from tests.engine.memory.conftest import Pair, make_pair, make_service, process

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 26, 3, 0, tzinfo=UTC)  # 2026-09-26（土）12:00 JST
EMBED = HashEmbedding(1536)


async def _insert(
    pair: Pair,
    content: str,
    *,
    kind: str = "fact",
    importance: float = 0.7,
    created_at: datetime = NOW,
    tags: list[str] | None = None,
    embed: bool = True,
    status: str = "active",
) -> uuid.UUID:
    literal = vector_literal((await EMBED.embed([content]))[0]) if embed else None
    memory_id: uuid.UUID = await pair.world.conn.fetchval(
        """
        insert into public.memories (user_id, character_id, kind, content, importance, tags, embedding,
                                     status, superseded_at, created_at, updated_at)
        values ($1, $2, $3, $4, $5, $6, $7::text::extensions.vector, $8,
                case when $8 = 'superseded' then $9::timestamptz end, $9, $9)
        returning id
        """,
        pair.user_id,
        pair.character_id,
        kind,
        content,
        importance,
        tags or [],
        literal,
        status,
        created_at,
    )
    return memory_id


async def _query(text: str) -> list[float]:
    return (await EMBED.embed([text]))[0]


async def test_relevant_memory_ranks_above_unrelated_ones(pool: Any, pair: Pair) -> None:
    service = make_service(pool)
    osaka = await _insert(pair, "ユーザーは来週、大阪に出張する", kind="episode", importance=0.8)
    for i, text in enumerate(["ユーザーはコーヒーが好き", "ユーザーは朝が弱い", "ユーザーは妹がいる"]):
        await _insert(pair, text, kind="preference", importance=0.9, created_at=NOW - timedelta(days=i))
    context = await service.retrieve_context(
        user_id=pair.user_id,
        character_id=pair.character_id,
        query_text="大阪のお土産、何がいいと思う？",
        query_embedding=await _query("大阪のお土産、何がいいと思う？"),
        now=NOW,
    )
    assert context.retrieval_skipped is False
    assert context.memories[0].id == osaka
    assert context.memories[0].score is not None
    assert len(context.memories) <= 10


async def test_pinned_relationship_core_facts_and_latest_summary_are_always_included(pool: Any, pair: Pair) -> None:
    service = make_service(pool)
    nickname = await _insert(pair, "ユーザーは「たっくん」と呼ばれたい", kind="relationship", importance=0.85)
    job = await _insert(pair, "ユーザーは銀行で働いている", importance=0.9)
    old_summary = await _insert(
        pair, "これまでの会話の要約（古い）", kind="summary", tags=["summary"], created_at=NOW - timedelta(days=9)
    )
    summary = await _insert(pair, "これまでの会話の要約", kind="summary", tags=["summary"], created_at=NOW)
    superseded = await _insert(pair, "ユーザーは広告代理店で働いている", importance=0.9, status="superseded")
    context = await service.retrieve_context(
        user_id=pair.user_id,
        character_id=pair.character_id,
        query_text="今日のごはん",
        query_embedding=await _query("今日のごはん"),
        now=NOW,
    )
    ids = [m.id for m in context.memories]
    assert ids[:2] == [nickname, job]  # 呼び方・重要な事実が先頭
    assert summary in ids
    assert old_summary in ids  # 文字数に余裕があれば 2 件目の要約も入る
    assert superseded not in ids  # 置き換えられた記憶は使わない
    assert ids.index(summary) < ids.index(old_summary)


async def test_character_budget_limits_memories(pool: Any, pair: Pair) -> None:
    service = make_service(pool, memory_chars_budget=60, max_memories=10)
    for i in range(8):
        await _insert(pair, f"ユーザーは大阪の話をした{i}回目の記憶です", importance=0.7)
    context = await service.retrieve_context(
        user_id=pair.user_id,
        character_id=pair.character_id,
        query_text="大阪",
        query_embedding=await _query("大阪"),
        now=NOW,
    )
    assert sum(len(m.content) for m in context.memories) <= 60
    assert 1 <= len(context.memories) < 8


async def test_retrieval_without_embedding_uses_importance_and_recency(pool: Any, pair: Pair) -> None:
    service = make_service(pool)
    important = await _insert(pair, "ユーザーは猫を飼っている", importance=0.75)
    await _insert(pair, "ユーザーは雨が苦手", importance=0.6, created_at=NOW - timedelta(days=200))
    context = await service.retrieve_context(
        user_id=pair.user_id,
        character_id=pair.character_id,
        query_text="ただいま",
        query_embedding=None,
        now=NOW,
    )
    assert context.retrieval_skipped is True
    assert context.memories[0].id == important


async def test_due_promises_within_two_days_by_jst_date(pool: Any, pair: Pair) -> None:
    service = make_service(pool)
    await process(service, pair, [await pair.turn("来週の木曜、面接なんだよね", at=NOW)], NOW)
    await process(service, pair, [await pair.turn("12月20日にライブに行く予定", at=NOW + timedelta(minutes=1))], NOW)
    promises = await pair.promises()
    assert len(promises) == 2
    # 面接（10/1）の 2 日前（9/29）→ 入る。ライブ（12/20）は入らない
    now = datetime(2026, 9, 29, 9, 0, tzinfo=JST)
    context = await service.retrieve_context(
        user_id=pair.user_id,
        character_id=pair.character_id,
        query_text="面接",
        query_embedding=await _query("面接"),
        now=now,
    )
    assert [p.content for p in context.promises] == ["面接"]
    # 約束の元になった記憶（絶対日付入り）も、話題に関係があれば記憶の欄に入る
    interview = next(p for p in promises if p["content"] == "面接")
    assert interview["source_memory_id"] in {m.id for m in context.memories}

    due = await service.due_promises(
        user_id=pair.user_id, character_id=pair.character_id, now=now, window=timedelta(days=3)
    )
    assert [p.content for p in due] == ["面接"]
    await service.mark_promise_mentioned(promise_id=due[0].id, now=now)
    await service.mark_promise_mentioned(promise_id=due[0].id, now=now + timedelta(hours=1))  # 2 回目は何もしない
    row = await pair.world.conn.fetchrow("select status, mentioned_at from public.promises where id = $1", due[0].id)
    assert row["status"] == "mentioned"
    assert row["mentioned_at"] == now
    changes = await pair.audit("promise.status_change")
    assert [(c["after"], c["source"]) for c in changes] == [("mentioned", "proactive")]


async def test_character_memories_pair_statements_and_recent_shared_events(pool: Any, world: World) -> None:
    service = make_service(pool)
    pair = await make_pair(world)
    other = await make_pair(world)
    conn = world.conn

    async def statement(user_id: uuid.UUID | None, content: str, occurred_at: datetime, kind: str) -> uuid.UUID:
        literal = vector_literal((await EMBED.embed([content]))[0])
        value: uuid.UUID = await conn.fetchval(
            """
            insert into public.character_memories (character_id, user_id, kind, content, occurred_at, embedding)
            values ($1, $2, $3, $4, $5, $6::text::extensions.vector) returning id
            """,
            pair.character_id,
            user_id,
            kind,
            content,
            occurred_at,
            literal,
        )
        return value

    mine = await statement(pair.user_id, "昨日、駅前のカフェに行った", NOW - timedelta(days=1), "self_statement")
    others = await statement(other.user_id, "猫を飼っている", NOW - timedelta(days=1), "self_statement")
    shared = await statement(None, "同期と新宿で飲み会", NOW - timedelta(days=2), "event")
    old_shared = await statement(None, "先月の旅行", NOW - timedelta(days=30), "event")
    future = await statement(None, "来週の旅行", NOW + timedelta(days=3), "event")
    context = await service.retrieve_context(
        user_id=pair.user_id,
        character_id=pair.character_id,
        query_text="カフェ",
        query_embedding=await _query("カフェ"),
        now=NOW,
    )
    ids = [c.id for c in context.character_memories]
    assert mine in ids
    assert shared in ids
    assert others not in ids  # 他のユーザーに話したことは使わない
    assert old_shared not in ids  # 共通の出来事は直近 14 日
    assert future not in ids  # 未来の出来事（時間の早送り）
    assert next(c for c in context.character_memories if c.id == shared).is_shared is True
    # 時系列順
    assert ids == sorted(ids, key=lambda i: next(c.occurred_at for c in context.character_memories if c.id == i))


async def test_mark_referenced_updates_counters_without_touching_updated_at(pool: Any, pair: Pair) -> None:
    service = make_service(pool)
    memory_id = await _insert(pair, "ユーザーは猫を飼っている")
    before = await pair.world.conn.fetchrow("select updated_at from public.memories where id = $1", memory_id)
    later = NOW + timedelta(days=3)
    await service.mark_referenced(memory_ids=[memory_id, memory_id], now=later)
    await service.mark_referenced(memory_ids=[memory_id], now=NOW)  # 古い時刻では巻き戻さない
    row = await pair.world.conn.fetchrow(
        "select updated_at, last_referenced_at, reference_count from public.memories where id = $1", memory_id
    )
    assert row["reference_count"] == 2
    assert row["last_referenced_at"] == later
    assert row["updated_at"] == before["updated_at"]
    await service.mark_referenced(memory_ids=[], now=later)  # 空でも失敗しない


async def test_recall_of_a_due_soon_promise_memory_by_place_name(pool: Any, pair: Pair) -> None:
    """回帰（Web の E2E A9）: 「来週、大阪に出張するんだ」→ 返答の後の分析 → 「大阪でおすすめの場所ある？」で
    その記憶が検索される（期日が ±2 日の約束の元の記憶も記憶の欄に入る。hash 埋め込みでも語の一致で拾う）。"""
    service = make_service(pool)
    await process(service, pair, [await pair.turn("来週、大阪に出張するんだ", at=NOW)], NOW)
    [memory] = await pair.memories()
    assert "大阪に出張する" in memory["content"]
    later = NOW + timedelta(hours=1)
    for i, filler in enumerate(["今日はいい天気だね", "お昼ごはん食べた？", "最近どう？", "眠くなってきた"]):
        at = NOW + timedelta(minutes=10 + i)
        await process(service, pair, [await pair.turn(filler, at=at)], at)
    query = "大阪でおすすめの場所ある？"
    context = await service.retrieve_context(
        user_id=pair.user_id,
        character_id=pair.character_id,
        query_text=query,
        query_embedding=await _query(query),
        now=later,
    )
    assert memory["id"] in [m.id for m in context.memories]
    assert [p.content for p in context.promises] == ["大阪に出張する"]  # 期日（来週）が近い約束
