"""監査ログの保持期間（AUDIT_LOG_RETENTION_DAYS）: 少しずつ消す・既定は消さない・定期実行 audit.cleanup の登録。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.container import periodic_tasks
from app.services.audit import PURGE_BATCH_SIZE, AuditLogger
from tests.conftest import make_settings

T0 = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)


class _FakePool:
    """purge の delete の戻り値（消した件数）を順に返す。"""

    def __init__(self, counts: list[int]) -> None:
        self.counts = list(counts)
        self.calls: list[tuple[Any, ...]] = []

    async def fetchval(self, sql: str, *args: object) -> int:
        self.calls.append(args)
        return self.counts.pop(0)


async def test_purge_deletes_in_batches_until_a_short_batch() -> None:
    pool = _FakePool([3, 3, 1])
    deleted = await AuditLogger(pool).purge_older_than(now=T0, retention=timedelta(days=30), batch_size=3)  # type: ignore[arg-type]
    assert deleted == 7
    assert pool.calls == [(T0 - timedelta(days=30), 3)] * 3


async def test_purge_stops_at_max_batches_and_continues_next_run() -> None:
    pool = _FakePool([3, 3, 3, 3])
    audit = AuditLogger(pool)  # type: ignore[arg-type]
    assert await audit.purge_older_than(now=T0, retention=timedelta(days=1), batch_size=3, max_batches=2) == 6
    assert len(pool.calls) == 2


async def test_audit_cleanup_task_is_registered_only_when_retention_is_set() -> None:
    pool = _FakePool([0])
    audit = AuditLogger(pool)  # type: ignore[arg-type]
    common: dict[str, Any] = {"jobs": object(), "calendar": None, "affinity": None, "proactive": None, "audit": audit}

    off = periodic_tasks(make_settings(), **common)  # 既定（0）は消さない
    assert [t.name for t in off] == ["jobs.cleanup"]

    on = periodic_tasks(make_settings(audit_log_retention_days=30), **common)
    assert [t.name for t in on] == ["jobs.cleanup", "audit.cleanup"]
    task = on[-1]
    assert task.run_immediately is False  # 起動直後には走らせない（毎日の時刻に）
    assert await task.run(T0) == 0
    assert pool.calls == [(T0 - timedelta(days=30), PURGE_BATCH_SIZE)]

    # audit を渡さなければ登録しない
    assert [
        t.name for t in periodic_tasks(make_settings(audit_log_retention_days=30), **{**common, "audit": None})
    ] == ["jobs.cleanup"]
