"""常駐ループ（ワーカー・スケジューラ）: 想定外の例外で止まらない・止まったらログに残る・各タスクに新しい時刻を渡す。

DB を使わない（キューとプールは偽物）。DB を使うループのテストは test_jobs.py / test_scheduler.py。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.engine.jobs import JobRegistry, Worker
from app.engine.scheduler import PeriodicTask, RunDueResult, Scheduler, every
from app.engine.types import ManualClock, SystemClock

T0 = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)


def _fields(record: logging.LogRecord) -> dict[str, Any]:
    fields: dict[str, Any] = getattr(record, "fields", {})
    return fields


# ---------------------------------------------------------------------------
# ワーカー
# ---------------------------------------------------------------------------


class _FlakyQueue:
    """最初の claim で ValueError（DB・OS 以外の例外）を出し、以後は空を返す。target 回目で reached を立てる。"""

    def __init__(self, target: int = 3) -> None:
        self.claims = 0
        self.reclaims = 0
        self.target = target
        self.reached = asyncio.Event()

    async def reclaim_stale(self, *, now: datetime, lock_timeout: timedelta) -> int:
        self.reclaims += 1
        return 0

    async def claim(self, **_: object) -> None:
        self.claims += 1
        if self.claims >= self.target:
            self.reached.set()
        if self.claims == 1:
            raise ValueError("payload decode failed")


async def test_worker_loop_survives_unexpected_errors(caplog: pytest.LogCaptureFixture) -> None:
    queue = _FlakyQueue()
    worker = Worker(
        queue=queue,  # type: ignore[arg-type]
        registry=JobRegistry(),
        clock=SystemClock(),
        poll_interval_seconds=0.01,
        worker_id="test",
    )
    with caplog.at_level(logging.ERROR, logger="everkano.engine.worker"):
        worker.start()
        try:
            await asyncio.wait_for(queue.reached.wait(), timeout=5.0)
        finally:
            await worker.stop()
    assert queue.claims >= 3  # 1 回目の例外の後も取りに行き続ける
    assert queue.reclaims >= 1
    failures = [r for r in caplog.records if r.message == "engine worker poll failed"]
    assert len(failures) == 1
    assert "ValueError" in _fields(failures[0])["error"]
    assert not any("ended unexpectedly" in r.message for r in caplog.records)  # stop() で終わった分は残さない


async def test_worker_logs_when_a_loop_ends_without_stop(caplog: pytest.LogCaptureFixture) -> None:
    worker = Worker(queue=None, registry=JobRegistry(), clock=SystemClock(), worker_id="test")  # type: ignore[arg-type]

    async def died() -> None:
        raise RuntimeError("loop died")

    task = asyncio.create_task(died(), name="engine-worker-0")
    with contextlib.suppress(RuntimeError):
        await task
    with caplog.at_level(logging.ERROR, logger="everkano.engine.worker"):
        worker._on_loop_done(task)
    [record] = [r for r in caplog.records if r.message == "engine worker loop ended unexpectedly"]
    assert "loop died" in _fields(record)["error"]
    assert _fields(record)["task"] == "engine-worker-0"


# ---------------------------------------------------------------------------
# スケジューラ
# ---------------------------------------------------------------------------


async def test_scheduler_loop_survives_unexpected_errors(caplog: pytest.LogCaptureFixture) -> None:
    scheduler = Scheduler(pool=None, clock=ManualClock(T0), audit=None, tasks=[], poll_interval_seconds=0.01)  # type: ignore[arg-type]
    calls = 0
    reached = asyncio.Event()

    async def flaky_run_due(*, now: datetime | None = None, only: Sequence[str] | None = None) -> RunDueResult:
        nonlocal calls
        calls += 1
        if calls >= 3:
            reached.set()
        if calls == 1:
            raise ValueError("unexpected")
        return RunDueResult(leader=False)

    scheduler.run_due = flaky_run_due  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR, logger="everkano.engine.scheduler"):
        scheduler.start()
        try:
            await asyncio.wait_for(reached.wait(), timeout=5.0)
        finally:
            await scheduler.stop()
    assert calls >= 3
    failures = [r for r in caplog.records if r.message == "scheduler poll failed"]
    assert len(failures) == 1
    assert "ValueError" in _fields(failures[0])["error"]
    assert not any("ended unexpectedly" in r.message for r in caplog.records)


async def test_scheduler_logs_when_the_loop_ends_without_stop(caplog: pytest.LogCaptureFixture) -> None:
    scheduler = Scheduler(pool=None, clock=ManualClock(T0), audit=None, tasks=[])  # type: ignore[arg-type]

    async def died() -> None:
        raise RuntimeError("loop died")

    task = asyncio.create_task(died())
    with contextlib.suppress(RuntimeError):
        await task
    with caplog.at_level(logging.ERROR, logger="everkano.engine.scheduler"):
        scheduler._on_loop_done(task)
    [record] = [r for r in caplog.records if r.message == "engine scheduler loop ended unexpectedly"]
    assert "loop died" in _fields(record)["error"]


class _SteppingClock:
    """now() を呼ぶたびに 1 分進む時計（各タスクの直前に取り直しているかを見る）。"""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        self._now += timedelta(minutes=1)
        return self._now


class _FakeConn:
    def __init__(self) -> None:
        self.schedule_writes: list[tuple[Any, ...]] = []

    async def fetchval(self, sql: str, *args: object) -> bool:
        return True  # pg_try_advisory_lock / pg_advisory_unlock

    async def fetch(self, sql: str, *args: object) -> list[Any]:
        return []  # engine_schedules に行が無い（初回）

    async def execute(self, sql: str, *args: object) -> None:
        self.schedule_writes.append(args)


class _FakePool:
    def __init__(self) -> None:
        self.conn = _FakeConn()

    @contextlib.asynccontextmanager
    async def acquire(self) -> AsyncIterator[_FakeConn]:
        yield self.conn


async def test_run_due_passes_a_fresh_now_to_each_task() -> None:
    clock = _SteppingClock(T0)
    seen: dict[str, datetime] = {}

    def task(name: str) -> PeriodicTask:
        async def run(now: datetime) -> str:
            seen[name] = now
            return name

        return PeriodicTask(name, run, every(timedelta(minutes=5)))

    pool = _FakePool()
    scheduler = Scheduler(pool=pool, clock=clock, audit=None, tasks=[task("a"), task("b")])  # type: ignore[arg-type]
    result = await scheduler.run_due()
    assert result.leader
    assert result.ran == ["a", "b"]
    assert seen["b"] > seen["a"]  # 後のタスクほど新しい時刻（前のタスクにかかった時間の分だけ古い時刻を渡さない）
    # engine_schedules の last_run_at もタスクごとの時刻
    recorded = {args[0]: args[1] for args in pool.conn.schedule_writes}
    assert recorded == seen

    # now を渡したら（テスト・評価ハーネス）全タスクに同じ時刻
    seen.clear()
    await scheduler.run_due(now=T0)
    assert seen == {"a": T0, "b": T0}
