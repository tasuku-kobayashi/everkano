"""監査ログ（H6 / BRIEF §2.9）。

- `audit_logs(event_type, user_id, character_id, payload jsonb)` に、チャットのトランザクションとは
  独立した接続で書き込む。同じ内容を JSON 1行として stdout にも出力する。
- payload には必ず `request_id` を含める。
- DB 書き込みに失敗してもリクエストは失敗させない。ただし握りつぶさず ERROR ログを出す。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Literal
from uuid import UUID

import asyncpg

from app.core.db import Pool
from app.core.logging import get_logger, request_id_var

logger = get_logger("audit")

AuditEventType = Literal[
    # --- MVP
    "chat.request",
    "chat.response",
    "moderation.flag",
    "llm.error",
    "conversation.create",
    "memory.create",
    "memory.update",
    "memory.delete",
    "memory.summary",
    "comment.create",
    "comment.generate",
    "auth.failure",
    # --- キャラクターエンジン v1.0（E9: 3つの仕組みの状態変化はすべて記録する）
    # 記憶（§4）
    "memory.supersede",
    "memory.tombstone_suppressed",
    "memory.user_edited_skipped",
    "memory.analysis",
    "memory.injection_skipped",
    "promise.create",
    "promise.update",
    "promise.status_change",
    "character_memory.create",
    # カレンダー（§5）
    "calendar.generate",
    "calendar.state_change",
    "calendar.event_done",
    "calendar.post_create",
    "calendar.promise_event",
    "calendar.conflict",
    # 好感度（§6）
    "affinity.update",
    "affinity.stage_change",
    "affinity.manipulation_detected",
    "affinity.decay",
    "affinity.skipped",
    # 自発メッセージ（§7）
    "proactive.send",
    "proactive.skipped",
    "proactive.dropped",
    "proactive.settings_update",
    # 安全対応（E6）・ジョブ基盤
    "safety.trigger",
    "engine.job_failed",
    "engine.job_dead",
    "engine.schedule_failed",
    "engine.context_degraded",
]

_INSERT_SQL: Final[str] = (
    "insert into public.audit_logs (event_type, user_id, character_id, payload, created_at)"
    " values ($1, $2, $3, $4, coalesce($5, now()))"
)


class AuditLogger:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def log(
        self,
        event_type: AuditEventType,
        *,
        user_id: UUID | None = None,
        character_id: UUID | None = None,
        payload: dict[str, Any] | None = None,
        at: datetime | None = None,
    ) -> None:
        """監査ログを1件書く。

        `at` はアプリの時計（Clock）の時刻。キャラクターエンジンは必ず渡す（評価ハーネスの時間の早送りで、
        ログの時刻もシミュレーション上の時刻になるようにする）。省略時は DB の now()。
        """
        body: dict[str, Any] = dict(payload or {})
        body.setdefault("request_id", request_id_var.get())
        if at is not None:
            body.setdefault("at", at.isoformat())
        logger.info(
            event_type,
            extra={
                "fields": {
                    "audit": True,
                    "event_type": event_type,
                    "user_id": str(user_id) if user_id else None,
                    "character_id": str(character_id) if character_id else None,
                    "payload": body,
                }
            },
        )
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(_INSERT_SQL, event_type, user_id, character_id, body, at)
        except (OSError, TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
            logger.error(
                "failed to write audit log to database",
                extra={
                    "fields": {
                        "event_type": event_type,
                        "user_id": str(user_id) if user_id else None,
                        "character_id": str(character_id) if character_id else None,
                        "error": repr(exc),
                        # INFO の複製がフィルタされても内容が失われないよう、本文も残す
                        "payload": body,
                    }
                },
            )
