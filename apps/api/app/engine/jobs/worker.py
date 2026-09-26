"""ジョブのハンドラ登録とワーカー（ENGINE_BRIEF §2.3）。

- `JobRegistry`: kind → ハンドラ。ハンドラは `JobContext`（ジョブ・時計の時刻）を受け取り、必要なら
  `JobResult(rerun_at=...)` で同じ内容の再実行を依頼する（処理しきれなかった残りがある場合など）。
- `Worker`:
  - `start()` / `stop()`: API プロセス内（ENGINE_WORKER_ENABLED）または `python -m app.worker` で動く常駐ループ。
    `ENGINE_WORKER_CONCURRENCY` 本のループが、それぞれ `FOR UPDATE SKIP LOCKED` でジョブを取り合う。
  - `run_until_idle(now=...)`: テスト・評価ハーネス用の決定的な実行。run_at <= now のジョブが無くなるまで
    1件ずつ順に処理する（ハンドラに渡す now も固定）。`dedupe_keys` / `kinds` で対象を絞れる
    （同じ DB を他のテストが同時に使うため）。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import asyncpg

from app.core.logging import get_logger
from app.engine.jobs.queue import Job, PgJobQueue
from app.engine.types import Clock

logger = get_logger("engine.worker")


class PermanentJobError(Exception):
    """再試行しても成功しない失敗（入力の不備など）。ジョブは failed になり再試行しない。"""


@dataclass(frozen=True, slots=True)
class JobContext:
    job: Job
    now: datetime

    @property
    def payload(self) -> dict[str, Any]:
        return self.job.payload


@dataclass(frozen=True, slots=True)
class JobResult:
    rerun_at: datetime | None = None  # 同じ内容でもう一度実行する（残りがある）
    detail: dict[str, Any] = field(default_factory=dict)


JobHandler = Callable[[JobContext], Awaitable[JobResult | None]]


class JobRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, kind: str, handler: JobHandler) -> None:
        if kind in self._handlers:
            raise ValueError(f"job handler already registered: {kind}")
        self._handlers[kind] = handler

    def get(self, kind: str) -> JobHandler | None:
        return self._handlers.get(kind)

    def kinds(self) -> list[str]:
        return sorted(self._handlers)


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


class Worker:
    def __init__(
        self,
        *,
        queue: PgJobQueue,
        registry: JobRegistry,
        clock: Clock,
        concurrency: int = 1,
        poll_interval_seconds: float = 1.0,
        job_timeout_seconds: float = 300.0,
        lock_timeout: timedelta = timedelta(minutes=10),
        worker_id: str | None = None,
    ) -> None:
        self._queue = queue
        self._registry = registry
        self._clock = clock
        self._concurrency = concurrency
        self._poll_interval = poll_interval_seconds
        self._job_timeout = job_timeout_seconds
        self._lock_timeout = lock_timeout
        self.worker_id = worker_id or default_worker_id()
        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = asyncio.Event()
        self._wakeup = asyncio.Event()

    # ------------------------------------------------------------------ 決定的な実行（テスト・評価）
    async def run_once(
        self,
        *,
        now: datetime | None = None,
        kinds: Sequence[str] | None = None,
        dedupe_keys: Sequence[str] | None = None,
        ignore_run_at: bool = False,
    ) -> Job | None:
        """実行可能なジョブを1件処理する。処理したジョブ（無ければ None）を返す。

        kinds を省略すると、ハンドラを登録済みの kind だけを取る（別の版のプロセスが登録した未知の kind を
        取って失敗扱いにしないため。ローリングデプロイ中に新旧のワーカーが並ぶ場合など）。
        """
        at = now or self._clock.now()
        job = await self._queue.claim(
            now=at,
            worker_id=self.worker_id,
            kinds=kinds if kinds is not None else self._registry.kinds(),
            dedupe_keys=dedupe_keys,
            ignore_run_at=ignore_run_at,
        )
        if job is None:
            return None
        await self._execute(job, at)
        return job

    async def run_until_idle(
        self,
        *,
        now: datetime | None = None,
        kinds: Sequence[str] | None = None,
        dedupe_keys: Sequence[str] | None = None,
        ignore_run_at: bool = False,
        max_jobs: int = 10_000,
    ) -> int:
        """run_at <= now（ignore_run_at なら時刻に関係なく）のジョブが無くなるまで処理し、件数を返す。

        失敗したジョブはバックオフで run_at が now より後になるため、同じ呼び出しの中で再実行されない。
        """
        processed = 0
        while processed < max_jobs:
            job = await self.run_once(now=now, kinds=kinds, dedupe_keys=dedupe_keys, ignore_run_at=ignore_run_at)
            if job is None:
                break
            processed += 1
        return processed

    # ------------------------------------------------------------------ 常駐ループ
    def start(self) -> None:
        if self._tasks:
            return
        self._stopping.clear()
        self._tasks = [
            asyncio.create_task(self._loop(index), name=f"engine-worker-{index}") for index in range(self._concurrency)
        ]
        logger.info(
            "engine worker started",
            extra={"fields": {"worker_id": self.worker_id, "concurrency": self._concurrency}},
        )

    def notify(self) -> None:
        """新しいジョブが登録されたことを知らせる（ポーリングの待ちを短縮する。同じプロセス内のみ）。"""
        self._wakeup.set()

    async def stop(self, timeout_seconds: float = 20.0) -> None:
        """新しいジョブを取らずに止める。実行中のジョブは timeout_seconds まで終わるのを待つ。"""
        if not self._tasks:
            return
        self._stopping.set()
        self._wakeup.set()
        _, pending = await asyncio.wait(self._tasks, timeout=timeout_seconds)
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        logger.info("engine worker stopped", extra={"fields": {"worker_id": self.worker_id}})

    async def _loop(self, index: int) -> None:
        last_reclaim = 0.0
        while not self._stopping.is_set():
            try:
                if index == 0 and time.monotonic() - last_reclaim > 60:
                    last_reclaim = time.monotonic()
                    await self._queue.reclaim_stale(now=self._clock.now(), lock_timeout=self._lock_timeout)
                job = await self.run_once()
            except (OSError, TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
                logger.error("engine worker poll failed", extra={"fields": {"error": repr(exc)}})
                job = None
            if job is not None:
                continue
            self._wakeup.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wakeup.wait(), timeout=self._poll_interval)

    # ------------------------------------------------------------------ 実行
    async def _execute(self, job: Job, now: datetime) -> None:
        handler = self._registry.get(job.kind)
        if handler is None:
            await self._queue.fail(job, now=now, error=f"no handler for job kind '{job.kind}'", permanent=True)
            return
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._job_timeout):
                result = await handler(JobContext(job=job, now=now))
        except PermanentJobError as exc:
            await self._queue.fail(job, now=now, error=f"{type(exc).__name__}: {exc}", permanent=True)
            return
        except asyncio.CancelledError:
            # 停止時の取り消し: 再試行できるよう queued に戻す（attempts は消費済み）
            with contextlib.suppress(Exception):
                await asyncio.shield(self._queue.fail(job, now=now, error="cancelled (worker stopping)"))
            raise
        except Exception as exc:
            await self._queue.fail(job, now=now, error=f"{type(exc).__name__}: {exc}")
            return
        await self._queue.complete(job, now=now, rerun_at=result.rerun_at if result else None)
        logger.info(
            "engine job done",
            extra={
                "fields": {
                    "job_id": job.id,
                    "kind": job.kind,
                    "attempts": job.attempts,
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    **(result.detail if result else {}),
                }
            },
        )
