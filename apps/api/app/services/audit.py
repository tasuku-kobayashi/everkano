"""監査ログ（H6 / BRIEF §2.9）。

- `audit_logs(event_type, user_id, character_id, payload jsonb)` に、チャットのトランザクションとは
  独立した接続で書き込む。同じ内容を JSON 1行として stdout にも出力する。
- payload には必ず `request_id` を含める。
- DB 書き込みに失敗してもリクエストは失敗させない。ただし握りつぶさず ERROR ログを出す。
"""

from __future__ import annotations

from typing import Any, Final, Literal
from uuid import UUID

import asyncpg

from app.core.db import Pool
from app.core.logging import get_logger, request_id_var

logger = get_logger("audit")

AuditEventType = Literal[
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
]

_INSERT_SQL: Final[str] = (
    "insert into public.audit_logs (event_type, user_id, character_id, payload) values ($1, $2, $3, $4)"
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
    ) -> None:
        body: dict[str, Any] = dict(payload or {})
        body.setdefault("request_id", request_id_var.get())
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
                await conn.execute(_INSERT_SQL, event_type, user_id, character_id, body)
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
