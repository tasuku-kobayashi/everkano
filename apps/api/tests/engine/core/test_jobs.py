"""Postgres のジョブキュー（登録の重複排除・デバウンス・再試行・dead・実行中の再実行依頼）とワーカー。"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from app.core.db import Pool
from app.engine.jobs import JobContext, JobRegistry, JobResult, PermanentJobError, PgJobQueue, Worker
from app.engine.types import ManualClock
from app.services.audit import AuditLogger

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)


@pytest.fixture
async def kind(pool: Pool) -> AsyncIterator[str]:
    """テストごとに一意の kind（同じ DB を他のテストが同時に使うため、取得もこの kind に絞る）。"""
    value = f"test.{uuid.uuid4().hex[:12]}"
    try:
        yield value
    finally:
        await pool.execute("delete from public.engine_jobs where kind like $1", value + "%")
        await pool.execute("delete from public.audit_logs where payload->>'kind' like $1", value + "%")


def _queue(pool: Pool, audit: AuditLogger, clock: ManualClock, **kwargs: float) -> PgJobQueue:
    return PgJobQueue(
        pool=pool,
        audit=audit,
        clock=clock,
        max_attempts=int(kwargs.get("max_attempts", 3)),
        backoff_base_seconds=kwargs.get("backoff", 30.0),
        backoff_max_seconds=kwargs.get("backoff_max", 3600.0),
    )


def _worker(queue: PgJobQueue, registry: JobRegistry, clock: ManualClock, worker_id: str = "w1") -> Worker:
    return Worker(queue=queue, registry=registry, clock=clock, worker_id=worker_id)


async def _rows(pool: Pool, kind: str) -> list[dict[str, object]]:
    rows = await pool.fetch(
        "select id, status, attempts, run_at, last_error, payload, created_at, finished_at"
        " from public.engine_jobs where kind = $1 order by id",
        kind,
    )
    return [dict(r) for r in rows]


async def test_enqueue_dedupe_and_debounce(pool: Pool, audit: AuditLogger, kind: str) -> None:
    clock = ManualClock(T0)
    queue = _queue(pool, audit, clock)
    first = await queue.enqueue(kind, {"n": 1}, run_at=T0 + timedelta(seconds=20), dedupe_key="c1")
    assert first is not None
    # 同じ dedupe_key の queued があれば登録しない（None）
    assert await queue.enqueue(kind, {"n": 2}, run_at=T0 + timedelta(seconds=25), dedupe_key="c1") is None
    rows = await _rows(pool, kind)
    assert len(rows) == 1
    assert rows[0]["run_at"] == T0 + timedelta(seconds=20)
    assert rows[0]["created_at"] == T0  # 時計の時刻で保存
    # デバウンス: run_at を後ろにずらす（ただし最初の登録から max_delay まで）
    assert (
        await queue.enqueue(
            kind, {}, run_at=T0 + timedelta(seconds=50), dedupe_key="c1", debounce_max_delay=timedelta(seconds=60)
        )
        is None
    )
    assert (await _rows(pool, kind))[0]["run_at"] == T0 + timedelta(seconds=50)
    await queue.enqueue(
        kind, {}, run_at=T0 + timedelta(seconds=500), dedupe_key="c1", debounce_max_delay=timedelta(seconds=60)
    )
    assert (await _rows(pool, kind))[0]["run_at"] == T0 + timedelta(seconds=60)
    # 別の dedupe_key / dedupe_key なしは登録される
    assert await queue.enqueue(kind, {}, run_at=T0, dedupe_key="c2") is not None
    assert await queue.enqueue(kind, {}, run_at=T0) is not None
    assert await queue.enqueue(kind, {}, run_at=T0) is not None
    assert len(await _rows(pool, kind)) == 4


async def test_run_until_idle_respects_run_at_and_filters(pool: Pool, audit: AuditLogger, kind: str) -> None:
    clock = ManualClock(T0)
    queue = _queue(pool, audit, clock)
    seen: list[tuple[object, datetime]] = []
    registry = JobRegistry()

    async def handler(ctx: JobContext) -> None:
        seen.append((ctx.payload["n"], ctx.now))

    registry.register(kind, handler)
    worker = _worker(queue, registry, clock)
    await queue.enqueue(kind, {"n": 1}, run_at=T0 + timedelta(seconds=20), dedupe_key="a")
    await queue.enqueue(kind, {"n": 2}, run_at=T0 + timedelta(minutes=5), dedupe_key="b")
    assert await worker.run_until_idle(now=T0, kinds=[kind]) == 0
    assert await worker.run_until_idle(now=T0 + timedelta(seconds=30), kinds=[kind]) == 1
    assert seen == [(1, T0 + timedelta(seconds=30))]
    # dedupe_keys で絞る / ignore_run_at で時刻に関係なく処理する
    assert await worker.run_until_idle(now=T0, kinds=[kind], dedupe_keys=["zzz"], ignore_run_at=True) == 0
    assert await worker.run_until_idle(now=T0, kinds=[kind], ignore_run_at=True) == 1
    rows = await _rows(pool, kind)
    assert [r["status"] for r in rows] == ["done", "done"]
    assert rows[0]["finished_at"] == T0 + timedelta(seconds=30)


async def test_retry_backoff_then_dead(pool: Pool, audit: AuditLogger, kind: str) -> None:
    clock = ManualClock(T0)
    queue = _queue(pool, audit, clock, max_attempts=3, backoff=30)
    registry = JobRegistry()
    calls: list[int] = []

    async def failing(ctx: JobContext) -> None:
        calls.append(ctx.job.attempts)
        raise RuntimeError("boom")

    registry.register(kind, failing)
    worker = _worker(queue, registry, clock)
    await queue.enqueue(kind, {"user_id": None}, run_at=T0, dedupe_key="x")
    assert await worker.run_until_idle(now=T0, kinds=[kind]) == 1  # 失敗後の run_at は now より後
    row = (await _rows(pool, kind))[0]
    assert (row["status"], row["attempts"]) == ("queued", 1)
    assert row["run_at"] == T0 + timedelta(seconds=30)
    assert "RuntimeError: boom" in str(row["last_error"])
    # 実行中・再試行待ちの間も dedupe は効く（二重に登録しない）
    assert await queue.enqueue(kind, {}, run_at=T0, dedupe_key="x") is None
    await worker.run_until_idle(now=T0 + timedelta(seconds=30), kinds=[kind])
    row = (await _rows(pool, kind))[0]
    assert (row["status"], row["attempts"], row["run_at"]) == ("queued", 2, T0 + timedelta(seconds=90))
    await worker.run_until_idle(now=T0 + timedelta(seconds=90), kinds=[kind])
    row = (await _rows(pool, kind))[0]
    assert (row["status"], row["attempts"]) == ("dead", 3)
    assert calls == [1, 2, 3]
    events = await pool.fetch(
        "select event_type, payload from public.audit_logs where payload->>'kind' = $1 order by id", kind
    )
    assert [e["event_type"] for e in events] == ["engine.job_failed", "engine.job_failed", "engine.job_dead"]
    # dead になったら同じ dedupe_key で新しく登録できる
    assert await queue.enqueue(kind, {}, run_at=T0, dedupe_key="x") is not None


async def test_permanent_failure_and_unknown_kind(pool: Pool, audit: AuditLogger, kind: str) -> None:
    clock = ManualClock(T0)
    queue = _queue(pool, audit, clock)
    registry = JobRegistry()

    async def bad_payload(ctx: JobContext) -> None:
        raise PermanentJobError("invalid conversation_id")

    registry.register(kind, bad_payload)
    worker = _worker(queue, registry, clock)
    await queue.enqueue(kind, {}, run_at=T0)
    await queue.enqueue(kind + ".unknown", {}, run_at=T0)
    await worker.run_until_idle(now=T0, kinds=[kind, kind + ".unknown"])
    assert [r["status"] for r in await _rows(pool, kind)] == ["failed"]
    assert [r["status"] for r in await _rows(pool, kind + ".unknown")] == ["failed"]


async def test_rerun_requested_while_running(pool: Pool, audit: AuditLogger, kind: str) -> None:
    """実行中に同じ dedupe_key で登録されたら、完了後にもう一度実行する（実行中に届いたターンを取りこぼさない）。"""
    clock = ManualClock(T0)
    queue = _queue(pool, audit, clock)
    registry = JobRegistry()
    runs: list[int] = []

    async def handler(ctx: JobContext) -> None:
        runs.append(ctx.job.id)
        if len(runs) == 1:
            # 実行中に次のターンが届く
            assert await queue.enqueue(kind, {"n": 2}, run_at=T0 + timedelta(seconds=20), dedupe_key="conv") is None

    registry.register(kind, handler)
    worker = _worker(queue, registry, clock)
    await queue.enqueue(kind, {"n": 1}, run_at=T0, dedupe_key="conv")
    assert await worker.run_until_idle(now=T0, kinds=[kind]) == 1
    rows = await _rows(pool, kind)
    assert [r["status"] for r in rows] == ["done", "queued"]
    assert rows[1]["run_at"] == T0 + timedelta(seconds=20)
    assert "_rerun_at" not in rows[1]["payload"]  # type: ignore[operator]
    assert await worker.run_until_idle(now=T0 + timedelta(seconds=20), kinds=[kind]) == 1
    assert len(runs) == 2


async def test_handler_can_request_rerun(pool: Pool, audit: AuditLogger, kind: str) -> None:
    clock = ManualClock(T0)
    queue = _queue(pool, audit, clock)
    registry = JobRegistry()
    remaining = [3]
    payloads: list[dict[str, object]] = []

    async def handler(ctx: JobContext) -> JobResult:
        payloads.append(dict(ctx.payload))
        remaining[0] -= 1
        await queue.update_payload(ctx.job, {"_done_steps": ["x"]})
        return JobResult(rerun_at=ctx.now if remaining[0] > 0 else None)

    registry.register(kind, handler)
    await queue.enqueue(kind, {"n": 1}, run_at=T0, dedupe_key="k")
    assert await _worker(queue, registry, clock).run_until_idle(now=T0, kinds=[kind]) == 3
    rows = await _rows(pool, kind)
    assert [r["status"] for r in rows] == ["done", "done", "done"]
    # 再実行のジョブには実行中の記録（_ で始まるキー）を引き継がない
    assert payloads == [{"n": 1}, {"n": 1}, {"n": 1}]


async def test_two_workers_never_process_the_same_job(pool: Pool, audit: AuditLogger, kind: str) -> None:
    clock = ManualClock(T0)
    queue = _queue(pool, audit, clock)
    registry = JobRegistry()
    processed: list[tuple[str, int]] = []

    async def handler(ctx: JobContext) -> None:
        await asyncio.sleep(0.01)
        processed.append((str(ctx.payload["n"]), ctx.job.id))

    registry.register(kind, handler)
    for n in range(20):
        await queue.enqueue(kind, {"n": n}, run_at=T0)
    workers = [_worker(queue, registry, clock, worker_id=f"w{i}") for i in range(3)]
    counts = await asyncio.gather(*(w.run_until_idle(now=T0, kinds=[kind]) for w in workers))
    assert sum(counts) == 20
    assert len({job_id for _, job_id in processed}) == 20
    assert all(r["status"] == "done" for r in await _rows(pool, kind))


async def test_reclaim_stale_and_cleanup(pool: Pool, audit: AuditLogger, kind: str) -> None:
    clock = ManualClock(T0)
    queue = _queue(pool, audit, clock, max_attempts=2)
    await queue.enqueue(kind, {}, run_at=T0)
    await queue.enqueue(kind, {}, run_at=T0)
    job = await queue.claim(now=T0, worker_id="crashed", kinds=[kind])
    assert job is not None
    # ロックの期限前は戻さない
    assert await queue.reclaim_stale(now=T0 + timedelta(minutes=5), lock_timeout=timedelta(minutes=10)) >= 0
    assert (await _rows(pool, kind))[0]["status"] == "running"
    await queue.reclaim_stale(now=T0 + timedelta(minutes=11), lock_timeout=timedelta(minutes=10))
    row = (await _rows(pool, kind))[0]
    assert (row["status"], row["attempts"]) == ("queued", 1)
    # 完了したジョブは保持期間を過ぎたら消す
    registry = JobRegistry()

    async def ok(ctx: JobContext) -> None:
        return None

    registry.register(kind, ok)
    await _worker(queue, registry, clock).run_until_idle(now=T0 + timedelta(minutes=11), kinds=[kind])
    assert await queue.cleanup(now=T0 + timedelta(days=1), retention=timedelta(days=7)) >= 0
    assert len(await _rows(pool, kind)) == 2
    await queue.cleanup(now=T0 + timedelta(days=8), retention=timedelta(days=7))
    assert await _rows(pool, kind) == []


async def test_background_loop_processes_jobs(pool: Pool, audit: AuditLogger, kind: str) -> None:
    clock = ManualClock(T0)
    queue = _queue(pool, audit, clock)
    registry = JobRegistry()
    done = asyncio.Event()

    async def handler(ctx: JobContext) -> None:
        done.set()

    registry.register(kind, handler)
    # 常駐ループはハンドラを登録した kind のジョブだけを取る（他のテストのジョブには触れない）
    worker = Worker(queue=queue, registry=registry, clock=clock, poll_interval_seconds=0.05, worker_id="loop")
    await queue.enqueue(kind, {}, run_at=T0)
    worker.start()
    try:
        await asyncio.wait_for(done.wait(), timeout=5)
    finally:
        await worker.stop()
    assert [r["status"] for r in await _rows(pool, kind)] == ["done"]
