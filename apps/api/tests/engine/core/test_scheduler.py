"""スケジューラ: engine_schedules の記録・期限・日次の時刻・リーダー選出（pg_try_advisory_lock）。"""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from app.core.db import Pool
from app.engine.scheduler import PeriodicTask, Scheduler, daily_at_jst, every
from app.engine.types import JST, ManualClock
from app.services.audit import AuditLogger

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)  # JST 12:00


@pytest.fixture
async def prefix(pool: Pool) -> AsyncIterator[str]:
    value = f"test.{uuid.uuid4().hex[:10]}"
    try:
        yield value
    finally:
        await pool.execute("delete from public.engine_schedules where name like $1", value + "%")
        await pool.execute("delete from public.audit_logs where payload->>'task' like $1", value + "%")


def _lock_key() -> int:
    # テストごとに別のロック（開発サーバーのスケジューラと取り合わない）
    return random.randint(1, 2**62)


def test_daily_at_jst() -> None:
    at4 = daily_at_jst(4)
    assert at4(datetime(2026, 10, 1, 18, 0, tzinfo=UTC)) == datetime(2026, 10, 2, 4, 0, tzinfo=JST)  # JST 03:00 → 同日
    assert at4(datetime(2026, 10, 1, 19, 0, tzinfo=UTC)) == datetime(2026, 10, 3, 4, 0, tzinfo=JST)  # JST 04:00 → 翌日
    assert every(timedelta(minutes=5))(T0) == T0 + timedelta(minutes=5)


async def test_run_due_runs_tasks_and_records_schedule(pool: Pool, audit: AuditLogger, prefix: str) -> None:
    clock = ManualClock(T0)
    ran: list[tuple[str, datetime]] = []

    def task(name: str) -> PeriodicTask:
        async def run(now: datetime) -> str:
            ran.append((name, now))
            return "ok"

        return PeriodicTask(f"{prefix}.{name}", run, every(timedelta(minutes=5)))

    async def daily_run(now: datetime) -> None:
        ran.append(("daily", now))

    tasks = [task("tick"), PeriodicTask(f"{prefix}.daily", daily_run, daily_at_jst(4), run_immediately=False)]
    scheduler = Scheduler(pool=pool, clock=clock, audit=audit, tasks=tasks, lock_key=_lock_key())
    result = await scheduler.run_due(now=T0)
    assert result.leader
    assert result.ran == [f"{prefix}.tick"]  # 日次のタスクは初回は予定時刻の記録だけ
    rows = {
        r["name"]: r for r in await pool.fetch("select * from public.engine_schedules where name like $1", prefix + "%")
    }
    assert rows[f"{prefix}.tick"]["last_run_at"] == T0
    assert rows[f"{prefix}.tick"]["next_run_at"] == T0 + timedelta(minutes=5)
    assert rows[f"{prefix}.daily"]["next_run_at"] == datetime(2026, 10, 2, 4, 0, tzinfo=JST)
    # 期限前は実行しない
    assert (await scheduler.run_due(now=T0 + timedelta(minutes=4))).ran == []
    assert (await scheduler.run_due(now=T0 + timedelta(minutes=5))).ran == [f"{prefix}.tick"]
    # 翌日 04:00 JST を過ぎたら日次のタスクも実行する（取りこぼした回をさかのぼらず、今の時刻で1回）
    later = datetime(2026, 10, 2, 5, 0, tzinfo=JST)
    assert (await scheduler.run_due(now=later)).ran == [f"{prefix}.tick", f"{prefix}.daily"]
    assert ran[-1] == ("daily", later)


async def test_failed_task_records_error_and_retries_sooner(pool: Pool, audit: AuditLogger, prefix: str) -> None:
    clock = ManualClock(T0)

    async def boom(now: datetime) -> None:
        raise RuntimeError("calendar down")

    name = f"{prefix}.hourly"
    scheduler = Scheduler(
        pool=pool,
        clock=clock,
        audit=audit,
        tasks=[PeriodicTask(name, boom, every(timedelta(hours=1)))],
        lock_key=_lock_key(),
    )
    result = await scheduler.run_due(now=T0)
    assert result.runs[0].ok is False
    row = await pool.fetchrow("select * from public.engine_schedules where name = $1", name)
    assert row is not None
    assert "calendar down" in row["last_error"]
    assert row["next_run_at"] == T0 + timedelta(minutes=10)
    event = await pool.fetchrow(
        "select payload from public.audit_logs where event_type = 'engine.schedule_failed' and payload->>'task' = $1",
        name,
    )
    assert event is not None


async def test_only_one_instance_runs_tasks(pool: Pool, audit: AuditLogger, prefix: str) -> None:
    """2つのインスタンスが同時に run_due しても、リーダーのロックを取れた方だけが実行する。"""
    clock = ManualClock(T0)
    lock_key = _lock_key()
    started = asyncio.Event()
    release = asyncio.Event()
    runs: list[str] = []

    async def slow(now: datetime) -> None:
        runs.append("slow")
        started.set()
        await release.wait()

    name = f"{prefix}.slow"
    tasks = [PeriodicTask(name, slow, every(timedelta(minutes=5)))]
    first = Scheduler(pool=pool, clock=clock, audit=audit, tasks=tasks, lock_key=lock_key)
    second = Scheduler(pool=pool, clock=clock, audit=audit, tasks=tasks, lock_key=lock_key)
    running = asyncio.create_task(first.run_due(now=T0))
    await asyncio.wait_for(started.wait(), timeout=5)
    blocked = await second.run_due(now=T0)
    assert blocked.leader is False
    assert blocked.ran == []
    release.set()
    done = await running
    assert done.leader
    assert done.ran == [name]
    # 終わればロックは解放される。記録済みの次回時刻より前なので、もう一方が取っても実行しない
    again = await second.run_due(now=T0 + timedelta(minutes=1))
    assert again.leader
    assert again.ran == []
    assert runs == ["slow"]


async def test_background_loop_start_stop(pool: Pool, audit: AuditLogger, prefix: str) -> None:
    clock = ManualClock(T0)
    ran = asyncio.Event()

    async def run(now: datetime) -> None:
        ran.set()

    scheduler = Scheduler(
        pool=pool,
        clock=clock,
        audit=audit,
        tasks=[PeriodicTask(f"{prefix}.loop", run, every(timedelta(minutes=5)))],
        poll_interval_seconds=0.05,
        lock_key=_lock_key(),
    )
    scheduler.start()
    try:
        await asyncio.wait_for(ran.wait(), timeout=5)
    finally:
        await scheduler.stop()
