"""定期実行（スケジューラ。ENGINE_BRIEF §2.3）。

- 各タスクの前回・次回の実行時刻は `engine_schedules` に記録する（プロセスが再起動しても間隔が保たれる）。
- リーダー選出: 実行の度に `pg_try_advisory_lock` を取れたプロセスだけが期限の来たタスクを実行する
  （API プロセス内と worker プロセスの両方で有効にしても二重に実行されない）。ロックは接続単位なので、
  実行が終わったら必ず解放する（プールに戻した接続にロックが残らないように）。
- 時刻は時計（Clock）から。`run_due(now=...)` はテスト・評価ハーネス用の決定的な実行（期限の来たタスクを
  1回ずつ実行する。取りこぼした回をさかのぼって実行はしない — 各タスクは「今の時刻」で必要な処理をする）。

タスク（ENGINE_BRIEF §2.3）:
  calendar.ensure_schedules（1時間ごと。7日先まで予定を生成）/ calendar.tick（5分ごと。状態・予定の完了・投稿）/
  proactive.scan（10分ごと。自発メッセージ）/ affinity.daily（毎日 04:00 JST。気まずさ・不満の減衰）/
  jobs.cleanup（毎日。古いジョブの削除・止まったジョブの回収）
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

import asyncpg

from app.core.db import Connection, Pool
from app.core.logging import get_logger
from app.engine.types import JST, Clock
from app.services.audit import AuditLogger

logger = get_logger("engine.scheduler")

# pg_try_advisory_lock のキー（'everkano.engine.scheduler' 固定の 64bit 値）
SCHEDULER_LOCK_KEY: Final[int] = 0x65766B_5343_4844
# 失敗したタスクを再実行するまでの待ち（間隔がこれより短いタスクは間隔どおり）
RETRY_AFTER_FAILURE: Final[timedelta] = timedelta(minutes=10)

TaskRunner = Callable[[datetime], Awaitable[object]]
NextRun = Callable[[datetime], datetime]


def _namespace_lock_key(namespace: str) -> int:
    digest = hashlib.sha256(f"everkano.engine.scheduler:{namespace}".encode()).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF


def every(interval: timedelta) -> NextRun:
    def next_run(now: datetime) -> datetime:
        return now + interval

    return next_run


def daily_at_jst(hour: int, minute: int = 0) -> NextRun:
    """毎日 JST の hour:minute（now より後の最初の時刻）。"""

    def next_run(now: datetime) -> datetime:
        local = now.astimezone(JST)
        candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local:
            candidate += timedelta(days=1)
        return candidate

    return next_run


@dataclass(slots=True)
class EngineScope:
    """評価ハーネス・テスト用: 定期実行の対象を絞る（None = 全員。本番のスケジューラでは使わない）。

    共有の DB で時計を早送りするとき、自分のキャラ・ユーザー以外の予定・自発メッセージ・好感度に触れないようにする。
    """

    character_ids: tuple[UUID, ...] | None = None
    user_ids: tuple[UUID, ...] | None = None


@dataclass(frozen=True, slots=True)
class PeriodicTask:
    name: str
    run: TaskRunner
    next_run: NextRun
    # 初回（engine_schedules に行が無い）に、すぐ実行するか / 次の予定時刻まで待つか
    run_immediately: bool = True


@dataclass(frozen=True, slots=True)
class TaskRun:
    name: str
    ok: bool
    elapsed_ms: int
    result: object = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RunDueResult:
    leader: bool
    runs: tuple[TaskRun, ...] = ()

    @property
    def ran(self) -> list[str]:
        return [r.name for r in self.runs]


class Scheduler:
    def __init__(
        self,
        *,
        pool: Pool,
        clock: Clock,
        audit: AuditLogger,
        tasks: Sequence[PeriodicTask],
        poll_interval_seconds: float = 30.0,
        lock_key: int | None = None,
        namespace: str = "",
    ) -> None:
        """namespace: engine_schedules の名前の接頭辞とロックを分ける（評価ハーネス・テスト用。通常は空）。"""
        names = [t.name for t in tasks]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate periodic task names: {names}")
        self._pool = pool
        self._clock = clock
        self._audit = audit
        self._namespace = f"{namespace}:" if namespace else ""
        self._tasks = tuple(replace(t, name=self._namespace + t.name) for t in tasks)
        self._poll_interval = poll_interval_seconds
        if lock_key is None:
            lock_key = SCHEDULER_LOCK_KEY if not namespace else _namespace_lock_key(namespace)
        self._lock_key = lock_key
        self._loop_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    @property
    def task_names(self) -> list[str]:
        return [t.name for t in self._tasks]

    # ------------------------------------------------------------------ 決定的な実行（テスト・評価）
    async def run_due(self, *, now: datetime | None = None, only: Sequence[str] | None = None) -> RunDueResult:
        """リーダーのロックを取れたら、期限の来たタスクを順に実行する。取れなければ何もしない（leader=False）。"""
        at = now or self._clock.now()
        async with self._pool.acquire() as conn:
            locked = await conn.fetchval("select pg_try_advisory_lock($1)", self._lock_key)
            if not locked:
                return RunDueResult(leader=False)
            try:
                runs = await self._run_due_locked(conn, at, only)
            finally:
                await conn.fetchval("select pg_advisory_unlock($1)", self._lock_key)
        return RunDueResult(leader=True, runs=tuple(runs))

    async def run_task(self, name: str, *, now: datetime | None = None) -> TaskRun:
        """期限に関係なく1つのタスクを実行する（運用・評価ハーネス用。リーダーのロックは取らない）。"""
        task = next((t for t in self._tasks if t.name in (name, self._namespace + name)), None)
        if task is None:
            raise KeyError(name)
        at = now or self._clock.now()
        async with self._pool.acquire() as conn:
            return await self._run_one(conn, task, at)

    async def _run_due_locked(self, conn: Connection, now: datetime, only: Sequence[str] | None) -> list[TaskRun]:
        rows = await conn.fetch(
            "select name, next_run_at from public.engine_schedules where name = any($1)", self.task_names
        )
        next_runs: dict[str, datetime | None] = {r["name"]: r["next_run_at"] for r in rows}
        runs: list[TaskRun] = []
        for task in self._tasks:
            if only is not None and task.name not in only and task.name.removeprefix(self._namespace) not in only:
                continue
            if task.name not in next_runs and not task.run_immediately:
                # 初回は予定時刻だけ記録する（例: 日次のタスクを起動直後に走らせない）
                await conn.execute(
                    """
                    insert into public.engine_schedules (name, next_run_at, updated_at) values ($1, $2, $3)
                    on conflict (name) do nothing
                    """,
                    task.name,
                    task.next_run(now),
                    now,
                )
                continue
            due_at = next_runs.get(task.name)
            if due_at is not None and due_at > now:
                continue
            runs.append(await self._run_one(conn, task, now))
        return runs

    async def _run_one(self, conn: Connection, task: PeriodicTask, now: datetime) -> TaskRun:
        started = time.perf_counter()
        error: str | None = None
        result: object = None
        try:
            result = await task.run(now)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:2000]
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        next_run = task.next_run(now)
        if error is not None:
            next_run = min(next_run, now + RETRY_AFTER_FAILURE)
        await conn.execute(
            """
            insert into public.engine_schedules (name, last_run_at, next_run_at, last_error, updated_at)
            values ($1, $2, $3, $4, $2)
            on conflict (name) do update
               set last_run_at = excluded.last_run_at, next_run_at = excluded.next_run_at,
                   last_error = excluded.last_error, updated_at = excluded.updated_at
            """,
            task.name,
            now,
            next_run,
            error,
        )
        if error is not None:
            logger.error("periodic task failed", extra={"fields": {"task": task.name, "error": error}})
            await self._audit.log(
                "engine.schedule_failed",
                payload={
                    "task": task.name,
                    "error": error,
                    "next_run_at": next_run.isoformat(),
                    "elapsed_ms": elapsed_ms,
                },
                at=now,
            )
        else:
            logger.info(
                "periodic task done",
                extra={"fields": {"task": task.name, "elapsed_ms": elapsed_ms, "result": repr(result)[:200]}},
            )
        return TaskRun(name=task.name, ok=error is None, elapsed_ms=elapsed_ms, result=result, error=error)

    # ------------------------------------------------------------------ 常駐ループ
    def start(self) -> None:
        if self._loop_task is not None:
            return
        self._stopping.clear()
        self._loop_task = asyncio.create_task(self._loop(), name="engine-scheduler")
        logger.info("engine scheduler started", extra={"fields": {"tasks": self.task_names}})

    async def stop(self, timeout_seconds: float = 20.0) -> None:
        if self._loop_task is None:
            return
        self._stopping.set()
        task, self._loop_task = self._loop_task, None
        try:
            await asyncio.wait_for(task, timeout=timeout_seconds)
        except TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        logger.info("engine scheduler stopped")

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_due()
            except (OSError, TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
                logger.error("scheduler poll failed", extra={"fields": {"error": repr(exc)}})
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=self._poll_interval)
