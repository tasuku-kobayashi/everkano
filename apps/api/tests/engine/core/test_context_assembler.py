"""Context Assembler: 呼び方の解決・予算・並行取得・失敗したモジュールの省略。"""

from __future__ import annotations

import itertools
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.db import Pool
from app.engine.context_assembler import (
    ContextAssembler,
    ContextBudget,
    basic_world_state,
    extract_user_name,
    fit_memories,
    fit_relationship,
    fit_world_state,
    is_history_anchor,
    persona_static_chars,
    resolve_call_user,
    stabilize_history_start,
)
from app.engine.types import (
    CharacterMemoryItem,
    CharacterStateSnapshot,
    MemoryContext,
    MemoryItem,
    PromiseItem,
    RelationshipGuidance,
)
from app.services.audit import AuditLogger
from app.services.moderation import Moderator
from app.services.persona import PersonaRepository, load_persona_file
from app.services.types import HistoryItem
from tests.conftest import FIXTURES_DIR, REPO_ROOT, World
from tests.engine.core.conftest import FakeAffinity, FakeCalendar, FakeMemory

NOW = datetime(2026, 10, 2, 12, 30, tzinfo=UTC)
PERSONA = load_persona_file(FIXTURES_DIR / "personas" / "test_persona.yaml")


def _memory(content: str, kind: str = "fact", *, days_ago: int = 1) -> MemoryItem:
    return MemoryItem(
        id=uuid.uuid4(),
        kind=kind,
        content=content,
        importance=0.8,
        tags=(),
        created_at=NOW - timedelta(days=days_ago),
    )


def _guidance(call_user: str = "{name}くん", stage: str = "friend") -> RelationshipGuidance:
    return RelationshipGuidance(
        stage=stage,
        stage_label_ja="友達",
        call_user=call_user,
        tone="タメ口",
        affection="気軽に甘える",
        topics=("仕事", "趣味", "週末の予定"),
        examples=("おつかれ〜", "それな", "また話そ"),
        notes=("3日ぶりで少し寂しかった",),
    )


STATE = CharacterStateSnapshot(
    activity="会社で仕事",
    location="渋谷のオフィス",
    mood="集中",
    busyness=2,
    status_label="仕事中",
    event_id=None,
    event_kind="routine",
    reply_style_hint="今は手短に",
    next_event="19:00ごろ退社",
    recent_events=tuple(f"{i}日前: 予定{i}" * 3 for i in range(1, 8)),
)


# ---------------------------------------------------------------------------
# 呼び方
# ---------------------------------------------------------------------------


def test_extract_user_name_from_memory_formats() -> None:
    assert extract_user_name([_memory("ユーザーは「たっくん」と呼ばれたい", "relationship")]) == ("たっくん", None)
    assert extract_user_name([_memory("ユーザーは「たっくんって呼んで」と話していた", "relationship")]) == (
        "たっくん",
        None,
    )
    assert extract_user_name([_memory("ユーザーは「名前はたかしです」と話していた")]) == (None, "たかし")
    assert extract_user_name([_memory("ユーザーの名前は拓也さん")]) == (None, "拓也")
    assert extract_user_name([_memory("ユーザーは「山田と申します」と話していた")]) == (None, "山田")
    assert extract_user_name([_memory("ユーザーは猫が好き", "preference")]) == (None, None)
    # 新しい関係性の記憶を優先
    older = _memory("ユーザーは「たっくん」と呼ばれたい", "relationship", days_ago=10)
    newer = _memory("ユーザーは「たっちゃん」と呼ばれたい", "relationship", days_ago=1)
    assert extract_user_name([older, newer])[0] == "たっちゃん"


def test_resolve_call_user() -> None:
    nickname = _memory("ユーザーは「たっくん」と呼ばれたい", "relationship")
    name = _memory("ユーザーは「名前はたかしです」と話していた")
    guidance, source = resolve_call_user(_guidance(), [nickname, name], PERSONA)
    assert (guidance.call_user, source) == ("たっくん", "nickname")
    guidance, source = resolve_call_user(_guidance(), [name], PERSONA)
    assert (guidance.call_user, source) == ("たかしくん", "name")
    guidance, source = resolve_call_user(_guidance(), [], PERSONA)
    assert source == "fallback"
    assert "{name}" not in guidance.call_user
    guidance, source = resolve_call_user(_guidance(call_user="きみ"), [name], PERSONA)
    assert (guidance.call_user, source) == ("きみ", None)


# ---------------------------------------------------------------------------
# 予算
# ---------------------------------------------------------------------------


def test_fit_world_state_trims_recent_events_first() -> None:
    state, used = fit_world_state(basic_world_state(NOW), STATE, 300)
    assert used <= 300
    assert state is not None
    assert state.activity == STATE.activity  # 今していることは残す
    assert len(state.recent_events) < len(STATE.recent_events)


def test_fit_relationship_keeps_notes() -> None:
    long = RelationshipGuidance(
        stage="close",
        stage_label_ja="気になる人",
        call_user="たっくん",
        tone="甘えた口調" * 10,
        affection="好意をにじませる" * 10,
        topics=tuple(f"話題{i}" * 5 for i in range(10)),
        examples=tuple(f"例文{i}" * 10 for i in range(10)),
        notes=("少し気まずさが残っている",),
    )
    fitted, used = fit_relationship(long, 400)
    assert used <= 400
    assert fitted.notes[0].startswith("少し気まずさ")


def test_fit_memories_limits_count_and_chars_and_keeps_relationship() -> None:
    memories = [_memory(f"ユーザーは事実{i}について長く話していた" * 3) for i in range(20)]
    memories.append(_memory("ユーザーは「たっくん」と呼ばれたい", "relationship"))
    kept, used = fit_memories(memories, 1200, 10)
    assert len(kept) <= 10
    assert used <= 1200
    assert any(m.kind == "relationship" for m in kept)
    # 元の順位の順を保つ
    order = [memories.index(m) for m in kept]
    assert order == sorted(order)


# ---------------------------------------------------------------------------
# assemble（DB の履歴 + フェイクのモジュール）
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.integration


async def _conversation(world: World) -> tuple[uuid.UUID, uuid.UUID]:
    user = await world.create_user()
    conversation_id = await world.conn.fetchval(
        "insert into public.conversations (user_id, character_id) values ($1, $2) returning id",
        user.id,
        world.character_id,
    )
    for i in range(30):
        await world.conn.execute(
            "insert into public.messages (conversation_id, sender_type, body, created_at) values ($1, $2, $3, $4)",
            conversation_id,
            "user" if i % 2 else "character",
            f"{i:02d}" + "あ" * 300,
            NOW - timedelta(minutes=60 - i),
        )
    await world.conn.execute(
        "insert into public.messages (conversation_id, sender_type, body, created_at) values ($1, 'user', $2, $3)",
        conversation_id,
        "中学生のころの話しよう",
        NOW - timedelta(seconds=10),
    )
    return user.id, conversation_id


def _assembler(pool: Pool, audit: AuditLogger, **modules: object) -> ContextAssembler:
    return ContextAssembler(
        pool=pool,
        moderator=Moderator(),
        audit=audit,
        memory=modules.get("memory"),  # type: ignore[arg-type]
        calendar=modules.get("calendar"),  # type: ignore[arg-type]
        affinity=modules.get("affinity"),  # type: ignore[arg-type]
        timeout_seconds=float(modules.get("timeout", 1.0)),  # type: ignore[arg-type]
    )


async def test_assemble_gathers_all_sections_within_budget(pool: Pool, audit: AuditLogger, world: World) -> None:
    user_id, conversation_id = await _conversation(world)
    facts = tuple(_memory(f"ユーザーは事実{i}について話していた" * 4) for i in range(15))
    memories = (*facts, _memory("ユーザーは「名前はたかしです」と話していた"))
    promise = PromiseItem(id=uuid.uuid4(), content="面接", due_at=NOW, due_precision="day", status="pending")
    trip = CharacterMemoryItem(id=uuid.uuid4(), kind="event", content="京都旅行", occurred_at=NOW, is_shared=True)
    memory = FakeMemory(context=MemoryContext(memories=memories, character_memories=(trip,), promises=(promise,)))
    assembler = _assembler(
        pool, audit, memory=memory, calendar=FakeCalendar(state=STATE), affinity=FakeAffinity(_guidance())
    )
    result = await assembler.assemble(
        user_id=user_id,
        character_id=world.character_id,
        conversation_id=conversation_id,
        persona=PERSONA,
        user_message="今日なにしてた？",
        now=NOW,
    )
    bundle = result.bundle
    report = bundle.budget_report
    budget = ContextBudget()
    assert result.degraded == ()
    assert bundle.state is not None
    assert bundle.relationship is not None
    assert bundle.relationship.call_user == "たかしくん"
    assert result.call_user_source == "name"
    assert report["world_state"] <= budget.world_state_chars
    assert report["relationship"] <= budget.relationship_chars
    assert report["memories"] <= budget.memories_chars
    assert report["memories_count"] <= budget.memories_max
    assert report["memories_dropped"] > 0
    assert report["history"] <= budget.history_chars
    assert report["history_dropped"] > 0
    assert report["promises_count"] == 1
    assert report["character_memories_count"] == 1
    # 新しい側を優先し、差し止めた発言は置き換える
    assert result.history[-1].body == "（不適切な発言のため省略）"
    assert set(result.timings_ms) == {"history", "memory", "calendar", "affinity"}


async def test_failing_or_slow_modules_are_omitted_and_audited(pool: Pool, audit: AuditLogger, world: World) -> None:
    user_id, conversation_id = await _conversation(world)
    assembler = _assembler(
        pool,
        audit,
        memory=FakeMemory(delay=5),  # 締め切り超過
        calendar=FakeCalendar(fail=True),  # 例外
        affinity=FakeAffinity(_guidance()),
        timeout=0.5,
    )
    started = time.perf_counter()
    result = await assembler.assemble(
        user_id=user_id,
        character_id=world.character_id,
        conversation_id=conversation_id,
        persona=PERSONA,
        user_message="こんにちは",
        now=NOW,
    )
    assert time.perf_counter() - started < 2
    assert result.degraded == ("calendar", "memory")
    assert result.bundle.state is None
    assert result.bundle.memory.memories == ()
    assert result.bundle.relationship is not None
    assert result.bundle.world.now == NOW  # 世界の時間は最小限のものを使う
    row = await world.conn.fetchrow(
        "select payload from public.audit_logs where event_type = 'engine.context_degraded' and user_id = $1", user_id
    )
    assert row is not None
    assert row["payload"]["sections"] == ["calendar", "memory"]
    assert "timeout" in row["payload"]["errors"]["memory"]


async def test_disabled_modules_are_not_called(pool: Pool, audit: AuditLogger, world: World) -> None:
    user_id, conversation_id = await _conversation(world)
    result = await _assembler(pool, audit).assemble(
        user_id=user_id,
        character_id=world.character_id,
        conversation_id=conversation_id,
        persona=PERSONA,
        user_message="こんにちは",
        now=NOW,
    )
    assert result.degraded == ()
    assert result.bundle.state is None
    assert result.bundle.relationship is None
    assert set(result.timings_ms) == {"history"}


def test_real_personas_static_part_fits_the_persona_budget() -> None:
    repo = PersonaRepository.load_dir(REPO_ROOT / "packages" / "personas")
    names = repo.keys()
    sizes = {key: persona_static_chars(repo.get(key)) for key in names}  # type: ignore[arg-type]
    # 予算は 1,800 字（超えてもプロンプトからは削らない。監査の budget_report に persona_over_budget が出る）
    assert max(sizes.values()) <= 1800, sizes


# ---------------------------------------------------------------------------
# 履歴の先頭をそろえる（プレフィックスキャッシュ）
# ---------------------------------------------------------------------------


def _item(index: int, anchor: bool) -> HistoryItem:
    # ID の整数値が 8 の倍数なら目印（is_history_anchor）
    value = (index + 1) * 8 if anchor else (index + 1) * 8 + 3
    return HistoryItem(
        id=uuid.UUID(int=value),
        sender_type="user" if index % 2 else "character",
        body=f"発言{index:03d}",
        created_at=NOW + timedelta(seconds=index),
    )


def test_history_start_is_stable_across_turns_when_the_window_slides() -> None:
    """取得の上限（60 件）で毎ターン古い側が 2 件ずつ落ちても、先頭は目印の発言のまま数ターン変わらない。"""
    conversation = [_item(i, anchor=i % 8 == 5) for i in range(200)]
    limit = 60
    starts: list[uuid.UUID] = []
    for end in range(120, 200, 2):  # 1 ターン = 2 件
        fetched = conversation[end - limit : end]
        window = stabilize_history_start(fetched, fetched, complete=False, anchor_every=8)
        assert window == fetched[len(fetched) - len(window) :]  # 末尾（最新）は必ず残る
        assert len(window) > limit - 8
        assert is_history_anchor(window[0], 8)
        starts.append(window[0].id)
    # 先頭が変わるのは 4 ターンに 1 回だけ（毎ターン変わるとキャッシュに当たらない）
    changes = sum(1 for a, b in itertools.pairwise(starts) if a != b)
    assert changes <= len(starts) // 4 + 1


def test_history_start_is_untouched_for_short_conversations_or_when_disabled() -> None:
    conversation = [_item(i, anchor=False) for i in range(10)]
    assert stabilize_history_start(conversation, conversation, complete=True, anchor_every=8) == conversation
    assert stabilize_history_start(conversation, conversation[4:], complete=True, anchor_every=1) == conversation[4:]
    # 目印が無ければ予算に収めた履歴のまま
    assert stabilize_history_start(conversation, conversation[4:], complete=False, anchor_every=8) == conversation[4:]
