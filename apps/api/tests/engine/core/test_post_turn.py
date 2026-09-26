"""post_turn ジョブ: ターンの読み込み・フラグ・モジュールの呼び出し順・処理済み位置・途中失敗からの再開。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from app.core.db import Pool
from app.engine.jobs import JOB_POST_TURN, EngineJobHandlers, JobRegistry, PgJobQueue, Worker
from app.engine.safety import DefaultSafetyService, load_safety_config
from app.engine.types import ManualClock
from app.services.audit import AuditLogger
from app.services.moderation import Moderator
from app.services.persona import PersonaRepository
from tests.conftest import FIXTURES_DIR, World, make_settings
from tests.engine.core.conftest import FakeAffinity, FakeCalendar, FakeMemory

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)


class Harness:
    def __init__(self, pool: Pool, audit: AuditLogger, **settings_overrides: object) -> None:
        self.clock = ManualClock(T0)
        self.settings = make_settings(**settings_overrides)
        self.memory = FakeMemory(promises=(uuid.uuid4(),))
        self.calendar = FakeCalendar()
        self.affinity = FakeAffinity()
        self.queue = PgJobQueue(pool=pool, audit=audit, clock=self.clock, max_attempts=3, backoff_base_seconds=10)
        self.handlers = EngineJobHandlers(
            settings=self.settings,
            pool=pool,
            queue=self.queue,
            personas=PersonaRepository.load_dir(FIXTURES_DIR / "personas"),
            moderator=Moderator(),
            safety=DefaultSafetyService(load_safety_config(self.settings.resolved_safety_resources_path)),
            memory=self.memory,
            calendar=self.calendar,
            affinity=self.affinity,
        )
        self.registry = JobRegistry()
        self.handlers.register(self.registry)
        self.worker = Worker(queue=self.queue, registry=self.registry, clock=self.clock, worker_id="test")

    async def enqueue(self, conversation_id: uuid.UUID, user_id: uuid.UUID, character_id: uuid.UUID) -> None:
        await self.queue.enqueue(
            JOB_POST_TURN,
            {"conversation_id": str(conversation_id), "user_id": str(user_id), "character_id": str(character_id)},
            run_at=T0,
            dedupe_key=str(conversation_id),
        )

    async def drain(self, conversation_id: uuid.UUID, now: datetime = T0) -> int:
        return await self.worker.run_until_idle(now=now, dedupe_keys=[str(conversation_id)])


@pytest.fixture
async def setup(world: World) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    user = await world.create_user()
    conversation_id = await world.conn.fetchval(
        "insert into public.conversations (user_id, character_id) values ($1, $2) returning id",
        user.id,
        world.character_id,
    )
    return user.id, conversation_id


async def _message(world: World, conversation_id: uuid.UUID, sender: str, body: str, minute: int, **kw: bool) -> None:
    await world.conn.execute(
        "insert into public.messages (conversation_id, sender_type, body, created_at, is_proactive, safety_triggered)"
        " values ($1, $2, $3, $4, $5, $6)",
        conversation_id,
        sender,
        body,
        T0 - timedelta(minutes=60 - minute),
        kw.get("proactive", False),
        kw.get("safety", False),
    )


async def test_post_turn_processes_turns_in_order_and_advances(
    pool: Pool, audit: AuditLogger, world: World, setup: tuple[uuid.UUID, uuid.UUID]
) -> None:
    user_id, conversation_id = setup
    h = Harness(pool, audit)
    await _message(world, conversation_id, "character", "はじめまして", 0)  # 挨拶（対にならない）
    await _message(world, conversation_id, "user", "来週の木曜に面接なんだ", 1)
    await _message(world, conversation_id, "character", "がんばってね", 2)
    await _message(world, conversation_id, "character", "ただいま〜", 3, proactive=True)  # 自発メッセージ
    await _message(world, conversation_id, "user", "中学生のころの話しよう", 4)  # Gate #1
    await _message(world, conversation_id, "character", "ごめんね、その話はちょっとできないかな", 5)
    await _message(world, conversation_id, "user", "もう死にたい", 6)  # E6
    await _message(world, conversation_id, "character", "話してくれてありがとう", 7)
    await h.enqueue(conversation_id, user_id, world.character_id)
    assert await h.drain(conversation_id) == 1

    [turns] = h.memory.processed
    assert [t.user_text for t in turns] == ["来週の木曜に面接なんだ", "中学生のころの話しよう", "もう死にたい"]
    assert [(t.moderated, t.safety_triggered) for t in turns] == [(False, False), (True, False), (False, True)]
    assert turns[0].occurred_at == T0 - timedelta(minutes=59)
    # 約束はカレンダーへ、好感度は差し止め・安全対応のターンを除いて評価
    assert h.calendar.synced == [h.memory.promises]
    assert [[t.user_text for t in batch] for batch in h.affinity.evaluated] == [["来週の木曜に面接なんだ"]]
    analyzed = await world.conn.fetchval(
        "select analyzed_until from public.conversations where id = $1", conversation_id
    )
    assert analyzed == T0 - timedelta(minutes=53)
    # 次のジョブは新しいターンだけを読む
    await _message(world, conversation_id, "user", "面接おわった！", 10)
    await _message(world, conversation_id, "character", "おつかれ！", 11)
    await h.enqueue(conversation_id, user_id, world.character_id)
    await h.drain(conversation_id)
    assert [t.user_text for t in h.memory.processed[1]] == ["面接おわった！"]


async def test_persisted_safety_flag_marks_the_turn(
    pool: Pool, audit: AuditLogger, world: World, setup: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """E6: 保存時の印（messages.safety_triggered）が正。検出語の無い言い回しでも、印のある返答のターンは評価しない。"""
    user_id, conversation_id = setup
    h = Harness(pool, audit)
    await _message(world, conversation_id, "user", "今日は散歩した", 1)
    await _message(world, conversation_id, "character", "いいね", 2)
    await _message(world, conversation_id, "user", "ちょっと遠くに行きたいな", 3)
    await _message(world, conversation_id, "character", "話してくれてありがとう", 4, safety=True)
    await h.enqueue(conversation_id, user_id, world.character_id)
    assert await h.drain(conversation_id) == 1
    [turns] = h.memory.processed
    assert [t.safety_triggered for t in turns] == [False, True]
    assert [[t.user_text for t in batch] for batch in h.affinity.evaluated] == [["今日は散歩した"]]


async def test_failed_step_is_retried_without_redoing_finished_steps(
    pool: Pool, audit: AuditLogger, world: World, setup: tuple[uuid.UUID, uuid.UUID]
) -> None:
    user_id, conversation_id = setup
    h = Harness(pool, audit)
    h.affinity.fail_evaluate = 1
    await _message(world, conversation_id, "user", "今日は仕事でほめられた", 1)
    await _message(world, conversation_id, "character", "すごい！", 2)
    await h.enqueue(conversation_id, user_id, world.character_id)
    await h.drain(conversation_id)
    status = await world.conn.fetchrow(
        "select status, attempts, payload from public.engine_jobs where dedupe_key = $1", str(conversation_id)
    )
    assert status is not None
    assert (status["status"], status["attempts"]) == ("queued", 1)
    assert status["payload"]["_done_steps"] == ["memory", "calendar"]
    # 再試行: 記憶・約束はやり直さず、好感度だけを行う
    await h.drain(conversation_id, now=T0 + timedelta(seconds=10))
    assert len(h.memory.processed) == 1
    assert len(h.calendar.synced) == 1
    assert len(h.affinity.evaluated) == 1
    assert await world.conn.fetchval(
        "select analyzed_until from public.conversations where id = $1", conversation_id
    ) == T0 - timedelta(minutes=58)


async def test_last_attempt_skips_the_failing_step_and_moves_on(
    pool: Pool, audit: AuditLogger, world: World, setup: tuple[uuid.UUID, uuid.UUID]
) -> None:
    user_id, conversation_id = setup
    h = Harness(pool, audit)
    h.memory.fail_process = 10  # 毎回失敗
    await _message(world, conversation_id, "user", "猫を飼ってるんだ", 1)
    await _message(world, conversation_id, "character", "いいなあ", 2)
    await h.enqueue(conversation_id, user_id, world.character_id)
    for step in range(3):
        await h.drain(conversation_id, now=T0 + timedelta(hours=step))
    job = await world.conn.fetchrow(
        "select status, attempts from public.engine_jobs where dedupe_key = $1", str(conversation_id)
    )
    assert job is not None
    assert (job["status"], job["attempts"]) == ("done", 3)
    # 記憶は諦めたが、好感度は評価して処理済みの位置も進めた（以後のジョブが同じターンで止まらない）
    assert h.memory.processed == []
    assert len(h.affinity.evaluated) == 1
    assert (
        await world.conn.fetchval("select analyzed_until from public.conversations where id = $1", conversation_id)
        is not None
    )


async def test_disabled_modules_are_skipped(
    pool: Pool, audit: AuditLogger, world: World, setup: tuple[uuid.UUID, uuid.UUID]
) -> None:
    user_id, conversation_id = setup
    h = Harness(pool, audit, engine_memory_enabled=False, engine_calendar_enabled=False)
    await _message(world, conversation_id, "user", "こんにちは", 1)
    await _message(world, conversation_id, "character", "こんにちは！", 2)
    await h.enqueue(conversation_id, user_id, world.character_id)
    await h.drain(conversation_id)
    assert h.memory.processed == []
    assert h.calendar.synced == []
    assert len(h.affinity.evaluated) == 1


async def test_many_turns_are_processed_in_batches(
    pool: Pool, audit: AuditLogger, world: World, setup: tuple[uuid.UUID, uuid.UUID]
) -> None:
    user_id, conversation_id = setup
    h = Harness(pool, audit, engine_post_turn_max_turns=3)
    for i in range(7):
        await _message(world, conversation_id, "user", f"発言{i}", i * 2 + 1)
        await _message(world, conversation_id, "character", f"返答{i}", i * 2 + 2)
    await h.enqueue(conversation_id, user_id, world.character_id)
    assert await h.drain(conversation_id) == 3  # 3 + 3 + 1 ターン（続きがあれば続けて実行する）
    assert [len(batch) for batch in h.memory.processed] == [3, 3, 1]
    assert [t.user_text for batch in h.memory.processed for t in batch] == [f"発言{i}" for i in range(7)]


async def test_deleted_conversation_is_a_no_op(pool: Pool, audit: AuditLogger, world: World) -> None:
    h = Harness(pool, audit)
    missing = uuid.uuid4()
    user = await world.create_user()
    await h.enqueue(missing, user.id, world.character_id)
    assert await h.drain(missing) == 1
    status = await world.conn.fetchval("select status from public.engine_jobs where dedupe_key = $1", str(missing))
    assert status == "done"


async def test_memory_summarize_job(
    pool: Pool, audit: AuditLogger, world: World, setup: tuple[uuid.UUID, uuid.UUID]
) -> None:
    user_id, conversation_id = setup
    h = Harness(pool, audit)
    await h.queue.enqueue(
        "memory.summarize",
        {"conversation_id": str(conversation_id), "user_id": str(user_id), "character_id": str(world.character_id)},
        run_at=T0,
        dedupe_key=str(conversation_id),
    )
    await h.drain(conversation_id)
    assert h.memory.summarized == [conversation_id]
