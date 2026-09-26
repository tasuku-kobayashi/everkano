"""好感度・自発メッセージのモジュールが外部に求める最小のインターフェース（テストでは偽物に差し替える）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.services.audit import AuditEventType


class AuditLog(Protocol):
    """app.services.audit.AuditLogger と同じ形（`at` はアプリの時計の時刻）。"""

    async def log(
        self,
        event_type: AuditEventType,
        *,
        user_id: UUID | None = None,
        character_id: UUID | None = None,
        payload: dict[str, Any] | None = None,
        at: datetime | None = None,
    ) -> None: ...
