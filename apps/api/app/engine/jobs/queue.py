"""Postgres のジョブキュー（`engine_jobs`。ENGINE_BRIEF §2.3。Redis などの追加の基盤は使わない）。

- 登録（enqueue）: `dedupe_key` 付きなら、同じ kind + dedupe_key の未処理（queued / running）ジョブは1件だけ
  （部分ユニークインデックス engine_jobs_dedupe_idx）。既にあれば登録せず None を返す。
  - `debounce_max_delay` を渡すと、queued のジョブの run_at を新しい run_at まで後ろにずらす（デバウンス）。
    ただし最初の登録から debounce_max_delay を超えては遅らせない（話し続けるユーザーでも処理が止まらない）。
  - 実行中（running）のジョブがあれば、そのジョブに「再実行の依頼」（payload._rerun_at）を付ける。
    完了時にワーカーが同じ内容で登録し直す（実行中に届いたターンを取りこぼさない）。
- 取得（claim）: `update ... where id = (select id ... for update skip locked limit 1) returning *`。
  複数のワーカー（API プロセス内・worker プロセス）が同時に動いても同じジョブを二重に取らない。
- 失敗: 指数バックオフで queued に戻す（audit `engine.job_failed`）。max_attempts に達したら dead
  （audit `engine.job_dead`）。再試行しても無駄な失敗（PermanentJobError・未登録の kind）は failed。
- 時刻はすべて呼び出し側の時計（Clock）の時刻を明示的に書く（評価ハーネスの時間の早送り）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

import asyncpg

from app.core.db import Pool
from app.core.logging import get_logger
from app.engine.types import Clock
from app.services.audit import AuditEventType, AuditLogger

logger = get_logger("engine.jobs")

RERUN_KEY: Final[str] = "_rerun_at"
# 同時に enqueue と完了が競合したときの再試行回数
_ENQUEUE_ATTEMPTS: Final[int] = 3

_JOB_COLUMNS: Final[str] = (
    "id, kind, dedupe_key, payload, run_at, status, attempts, max_attempts, last_error, created_at, locked_by"
)

_INSERT_SQL: Final[str] = f"""
insert into public.engine_jobs (kind, dedupe_key, payload, run_at, max_attempts, created_at, updated_at)
values ($1, $2, $3, $4, $5, $6, $6)
on conflict (kind, dedupe_key) where dedupe_key is not null and status in ('queued', 'running') do nothing
returning {_JOB_COLUMNS}
"""  # noqa: S608 - 列名は定数

_DEBOUNCE_SQL: Final[str] = """
update public.engine_jobs
   set run_at = least(greatest(run_at, $3), created_at + $4::interval)
 where kind = $1 and dedupe_key = $2 and status = 'queued'
returning id
"""

_TOUCH_QUEUED_SQL: Final[str] = """
select id from public.engine_jobs where kind = $1 and dedupe_key = $2 and status = 'queued'
"""

_MARK_RERUN_SQL: Final[str] = """
update public.engine_jobs
   set payload = payload || jsonb_build_object($3::text, $4::text)
 where kind = $1 and dedupe_key = $2 and status = 'running'
returning id
"""

_CLAIM_SQL: Final[str] = f"""
update public.engine_jobs j
   set status = 'running', attempts = j.attempts + 1, locked_at = $1, locked_by = $2
 where j.id = (
   select id from public.engine_jobs
    where status = 'queued'
      and ($3::boolean or run_at <= $1)
      and ($4::text[] is null or kind = any($4::text[]))
      and ($5::text[] is null or dedupe_key = any($5::text[]))
    order by run_at, id
    for update skip locked
    limit 1
 )
returning {_JOB_COLUMNS}
"""  # noqa: S608 - 列名は定数

_COMPLETE_SQL: Final[str] = """
update public.engine_jobs
   set status = 'done', finished_at = $2, locked_at = null, last_error = null
 where id = $1 and status = 'running'
returning payload
"""

_RETRY_SQL: Final[str] = """
update public.engine_jobs
   set status = 'queued', run_at = $2, last_error = $3, locked_at = null, locked_by = null
 where id = $1 and status = 'running'
"""

_FINISH_FAILED_SQL: Final[str] = """
update public.engine_jobs
   set status = $2, finished_at = $3, last_error = $4, locked_at = null
 where id = $1 and status = 'running'
returning payload
"""

_UPDATE_PAYLOAD_SQL: Final[str] = """
update public.engine_jobs set payload = payload || $2 where id = $1 and status = 'running'
"""

_RECLAIM_SQL: Final[str] = """
update public.engine_jobs
   set status = case when attempts >= max_attempts then 'dead' else 'queued' end,
       finished_at = case when attempts >= max_attempts then $1::timestamptz else null end,
       last_error = 'lock timeout (worker stopped while running)',
       locked_at = null, locked_by = null, run_at = $1::timestamptz
 where status = 'running' and locked_at < $2::timestamptz
returning id, kind, status
"""

_CLEANUP_SQL: Final[str] = """
with deleted as (
  delete from public.engine_jobs
   where status in ('done', 'failed', 'dead') and finished_at < $1
   returning 1
)
select count(*) from deleted
"""


@dataclass(frozen=True, slots=True)
class Job:
    id: int
    kind: str
    dedupe_key: str | None
    payload: dict[str, Any]
    run_at: datetime
    attempts: int
    max_attempts: int
    created_at: datetime


def _job(row: asyncpg.Record) -> Job:
    payload = row["payload"]
    if isinstance(payload, str):  # jsonb のコーデックが無い接続（テストの直接接続など）
        payload = json.loads(payload)
    return Job(
        id=row["id"],
        kind=row["kind"],
        dedupe_key=row["dedupe_key"],
        payload=dict(payload or {}),
        run_at=row["run_at"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        created_at=row["created_at"],
    )


def _json_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        loaded = json.loads(value)
        return dict(loaded) if isinstance(loaded, dict) else {}
    return dict(value or {})


class PgJobQueue:
    """JobQueue（app/engine/types.py）の Postgres 実装。"""

    def __init__(
        self,
        *,
        pool: Pool,
        audit: AuditLogger,
        clock: Clock,
        max_attempts: int = 5,
        backoff_base_seconds: float = 30.0,
        backoff_max_seconds: float = 3600.0,
    ) -> None:
        self._pool = pool
        self._audit = audit
        self._clock = clock
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base_seconds
        self._backoff_max = backoff_max_seconds

    # ------------------------------------------------------------------ 登録
    async def enqueue(
        self,
        kind: str,
        payload: dict[str, object],
        *,
        run_at: datetime,
        dedupe_key: str | None = None,
        debounce_max_delay: timedelta | None = None,
        max_attempts: int | None = None,
    ) -> int | None:
        """ジョブを登録する。同じ kind + dedupe_key の未処理ジョブがあれば登録せず None。"""
        now = self._clock.now()
        attempts = max_attempts or self._max_attempts
        for _ in range(_ENQUEUE_ATTEMPTS):
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(_INSERT_SQL, kind, dedupe_key, payload, run_at, attempts, now)
                if row is not None:
                    return int(row["id"])
                if dedupe_key is None:  # pragma: no cover - dedupe_key なしは衝突しない
                    return None
                if debounce_max_delay is not None:
                    if await conn.fetchval(_DEBOUNCE_SQL, kind, dedupe_key, run_at, debounce_max_delay) is not None:
                        return None
                elif await conn.fetchval(_TOUCH_QUEUED_SQL, kind, dedupe_key) is not None:
                    return None
                if await conn.fetchval(_MARK_RERUN_SQL, kind, dedupe_key, RERUN_KEY, run_at.isoformat()) is not None:
                    return None
            # 確認の間に完了した（どちらの update も 0 件）→ もう一度登録を試みる
        logger.warning("enqueue raced with completion repeatedly", extra={"fields": {"kind": kind}})
        return None

    # ------------------------------------------------------------------ 取得・完了
    async def claim(
        self,
        *,
        now: datetime,
        worker_id: str,
        kinds: Sequence[str] | None = None,
        dedupe_keys: Sequence[str] | None = None,
        ignore_run_at: bool = False,
    ) -> Job | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                _CLAIM_SQL,
                now,
                worker_id,
                ignore_run_at,
                list(kinds) if kinds is not None else None,
                list(dedupe_keys) if dedupe_keys is not None else None,
            )
        return _job(row) if row is not None else None

    async def complete(self, job: Job, *, now: datetime, rerun_at: datetime | None = None) -> None:
        """完了にする。実行中に再実行の依頼が付いていれば（または rerun_at があれば）同じ内容で登録し直す。"""
        async with self._pool.acquire() as conn:
            payload = await conn.fetchval(_COMPLETE_SQL, job.id, now)
        requested = _json_payload(payload).get(RERUN_KEY) if payload is not None else None
        candidates = [t for t in (rerun_at, _parse_time(requested)) if t is not None]
        if candidates:
            await self.enqueue(
                job.kind, public_payload(job.payload), run_at=max(*candidates, now), dedupe_key=job.dedupe_key
            )

    async def update_payload(self, job: Job, values: Mapping[str, object]) -> None:
        """実行中のジョブの payload に値を足す（途中まで終えた手順の記録。再試行で二重に行わないため）。"""
        async with self._pool.acquire() as conn:
            await conn.execute(_UPDATE_PAYLOAD_SQL, job.id, dict(values))

    async def fail(self, job: Job, *, now: datetime, error: str, permanent: bool = False) -> str:
        """失敗を記録する。戻り値は新しい状態（queued = 再試行待ち / dead / failed）。"""
        error = error[:2000]
        base_payload: dict[str, Any] = {
            "job_id": job.id,
            "kind": job.kind,
            "dedupe_key": job.dedupe_key,
            "attempts": job.attempts,
            "max_attempts": job.max_attempts,
            "error": error,
        }
        if permanent or job.attempts >= job.max_attempts:
            status = "failed" if permanent else "dead"
            async with self._pool.acquire() as conn:
                payload = await conn.fetchval(_FINISH_FAILED_SQL, job.id, status, now, error)
            event: AuditEventType = "engine.job_dead" if status == "dead" else "engine.job_failed"
            await self._audit.log(
                event,
                user_id=_uuid_or_none(job.payload.get("user_id")),
                character_id=_uuid_or_none(job.payload.get("character_id")),
                payload={**base_payload, "status": status, "permanent": permanent},
                at=now,
            )
            logger.error("engine job gave up", extra={"fields": {**base_payload, "status": status}})
            # 実行中に再実行の依頼が届いていれば、新しいターンのために登録し直す（失敗したジョブ自体は再実行しない）
            requested = _parse_time(_json_payload(payload).get(RERUN_KEY)) if payload is not None else None
            if requested is not None:
                await self.enqueue(
                    job.kind, public_payload(job.payload), run_at=max(requested, now), dedupe_key=job.dedupe_key
                )
            return status
        delay = self.backoff_seconds(job.attempts)
        next_run = now + timedelta(seconds=delay)
        async with self._pool.acquire() as conn:
            await conn.execute(_RETRY_SQL, job.id, next_run, error)
        await self._audit.log(
            "engine.job_failed",
            user_id=_uuid_or_none(job.payload.get("user_id")),
            character_id=_uuid_or_none(job.payload.get("character_id")),
            payload={**base_payload, "status": "queued", "will_retry": True, "next_run_at": next_run.isoformat()},
            at=now,
        )
        logger.warning("engine job failed; will retry", extra={"fields": {**base_payload, "delay_s": delay}})
        return "queued"

    def backoff_seconds(self, attempts: int) -> float:
        """attempts 回目の失敗の後の待ち（指数バックオフ。テスト・評価で再現できるようジッターは入れない）。"""
        return float(min(self._backoff_base * (2 ** max(attempts - 1, 0)), self._backoff_max))

    # ------------------------------------------------------------------ 保守
    async def reclaim_stale(self, *, now: datetime, lock_timeout: timedelta) -> int:
        """running のまま止まったジョブ（プロセスの強制終了など）を queued（上限に達していれば dead）に戻す。"""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_RECLAIM_SQL, now, now - lock_timeout)
        for row in rows:
            logger.warning("reclaimed stale engine job", extra={"fields": dict(row)})
            if row["status"] == "dead":
                await self._audit.log(
                    "engine.job_dead",
                    payload={"job_id": row["id"], "kind": row["kind"], "error": "lock timeout"},
                    at=now,
                )
        return len(rows)

    async def cleanup(self, *, now: datetime, retention: timedelta) -> int:
        """完了・失敗から retention 以上たったジョブを消す。"""
        async with self._pool.acquire() as conn:
            deleted = await conn.fetchval(_CLEANUP_SQL, now - retention)
        return int(deleted or 0)


def public_payload(payload: Mapping[str, Any]) -> dict[str, object]:
    """登録時の payload（`_` で始まる実行中の記録 — 再実行の依頼・済んだ手順 — を除いたもの）。"""
    return {k: v for k, v in payload.items() if not k.startswith("_")}


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _uuid_or_none(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return None
    return None
