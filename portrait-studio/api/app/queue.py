"""Serial job queue (MAX_CONCURRENCY = 1): one asyncio worker executes jobs in submission order."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime

from app.comfy_client import ComfyError, JobCanceled
from app.db import Database
from app.jobs import JobFailed, JobRunner
from app.models import Job, utcnow

logger = logging.getLogger("portrait.queue")


class JobQueue:
    def __init__(self, db: Database, runner: JobRunner) -> None:
        self.db = db
        self.runner = runner
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._cancel: dict[str, asyncio.Event] = {}
        self._task: asyncio.Task[None] | None = None
        self._running_id: str | None = None
        self._order: list[str] = []

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="portrait-job-queue")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    @property
    def running_id(self) -> str | None:
        return self._running_id

    def submit(self, job: Job) -> int:
        self.db.insert_job(job)
        self._cancel[job.id] = asyncio.Event()
        self._order.append(job.id)
        self._queue.put_nowait(job.id)
        return self.position(job) or 0

    def position(self, job: Job) -> int | None:
        """0 = running (or next to run when nothing runs); N = N jobs ahead. None when the job is finished."""
        if job.status == "running":
            return 0
        if job.status != "queued":
            return None
        ahead = self.db.queued_jobs_before(job)
        return ahead + (1 if self._running_id and self._running_id != job.id else 0)

    async def cancel(self, job_id: str) -> Job | None:
        job = self.db.get_job(job_id)
        if job is None:
            return None
        if job.status == "queued":
            job.status = "canceled"
            job.finished_at = utcnow()
            job.error = None
            self.db.update_job(job)
            self._cancel.pop(job_id, None)
            self.runner.write_audit(job)
            return job
        if job.status == "running":
            event = self._cancel.get(job_id)
            if event is not None:
                event.set()
            await self.runner.s.comfy.interrupt()
            return job
        return job

    def _save(self, job: Job) -> None:
        self.db.update_job(job)

    async def _loop(self) -> None:
        while True:
            job_id = await self._queue.get()
            job = self.db.get_job(job_id)
            if job is None or job.status != "queued":
                continue
            cancel = self._cancel.setdefault(job_id, asyncio.Event())
            job.status = "running"
            job.started_at = utcnow()
            self._running_id = job_id
            self._save(job)
            try:
                await self.runner.run(job, cancel, self._save)
                job.status = "canceled" if cancel.is_set() and not job.result_image_ids else "done"
            except JobCanceled:
                job.status = "canceled"
            except (JobFailed, ComfyError) as exc:
                job.status = "error"
                job.error = str(exc)
                logger.warning("job %s failed: %s", job_id, exc)
            except asyncio.CancelledError:
                job.status = "error"
                job.error = "サーバーの停止により中断されました。"
                job.finished_at = utcnow()
                self._save(job)
                raise
            except Exception as exc:
                job.status = "error"
                job.error = f"{exc.__class__.__name__}: {exc}"
                logger.exception("job %s crashed", job_id)
            finally:
                self._running_id = None
                self._cancel.pop(job_id, None)
            job.finished_at = utcnow()
            self._save(job)
            try:
                self.runner.write_audit(job)
            except Exception:
                logger.exception("audit write failed for job %s", job_id)


def elapsed_ms(started: datetime | None, finished: datetime | None) -> int | None:
    if started is None or finished is None:
        return None
    return int((finished - started).total_seconds() * 1000)
