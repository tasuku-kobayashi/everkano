"""好感度エンジンのテストの補助（偽物の監査ログ・ペルソナ・ターンの生成）。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.engine.types import JST, TurnRecord
from app.services.audit import AuditEventType
from app.services.persona import Persona, load_persona_file
from tests.conftest import FIXTURES_DIR

TEST_PERSONA_PATH = FIXTURES_DIR / "personas" / "test_persona.yaml"


@dataclass
class FakeAudit:
    """AuditLog の偽物（書いたイベントを覚える）。"""

    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def log(
        self,
        event_type: AuditEventType,
        *,
        user_id: uuid.UUID | None = None,
        character_id: uuid.UUID | None = None,
        payload: dict[str, Any] | None = None,
        at: datetime | None = None,
    ) -> None:
        self.events.append((event_type, dict(payload or {})))

    def of(self, event_type: str) -> list[dict[str, Any]]:
        return [payload for kind, payload in self.events if kind == event_type]


def load_test_persona() -> Persona:
    return load_persona_file(TEST_PERSONA_PATH)


def persona_with(**affinity: Any) -> Persona:
    """テスト用ペルソナの affinity（sensitivity / stage_pace / expression_delay）を差し替えたもの。"""
    persona = load_test_persona()
    engine = persona.engine
    assert engine is not None
    updates: dict[str, Any] = {}
    sensitivity = affinity.pop("sensitivity", None)
    if sensitivity is not None:
        updates["sensitivity"] = engine.affinity.sensitivity.model_copy(update=sensitivity)
    updates.update(affinity)
    new_affinity = engine.affinity.model_copy(update=updates)
    return persona.model_copy(update={"engine": engine.model_copy(update={"affinity": new_affinity})})


def make_turn(
    user_text: str,
    *,
    at: datetime,
    user_id: uuid.UUID,
    character_id: uuid.UUID,
    conversation_id: uuid.UUID | None = None,
    reply_text: str = "うん、聞かせて",
    moderated: bool = False,
    safety: bool = False,
) -> TurnRecord:
    return TurnRecord(
        conversation_id=conversation_id or uuid.UUID(int=1),
        user_id=user_id,
        character_id=character_id,
        user_message_id=uuid.uuid4(),
        character_message_id=uuid.uuid4(),
        user_text=user_text,
        reply_text=reply_text,
        occurred_at=at,
        moderated=moderated,
        safety_triggered=safety,
    )


def jst(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    """JST の日時 → UTC の aware datetime。"""
    return datetime(year, month, day, hour, minute, tzinfo=JST).astimezone(UTC)
