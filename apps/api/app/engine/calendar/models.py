"""カレンダーの内部データ（DB の行・生成した予定を同じ形で扱うためのビュー）。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Final
from uuid import UUID

import asyncpg

from app.engine.calendar.generator import PlannedEvent

EVENT_COLUMNS: Final[str] = (
    "id, character_id, kind, title, description, location, starts_at, ends_at, mood, busyness, visibility,"
    " user_id, participants, status, source, source_key, generated_for, post_id, meta, created_at"
)


@dataclass(frozen=True, slots=True)
class EventView:
    """キャラの予定 1 件（DB の character_events の行、または DB に入る前の生成結果）。"""

    id: UUID | None
    kind: str
    title: str
    starts_at: datetime
    ends_at: datetime
    busyness: int
    source: str
    source_key: str | None
    generated_for: date | None
    location: str | None = None
    description: str | None = None
    mood: str | None = None
    visibility: str = "public"
    status: str = "scheduled"
    user_id: UUID | None = None
    participants: tuple[UUID, ...] = ()
    post_id: UUID | None = None
    meta: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None

    @property
    def status_label(self) -> str | None:
        label = self.meta.get("status_label")
        return label if isinstance(label, str) and label else None

    @property
    def post_tags(self) -> tuple[str, ...]:
        tags = self.meta.get("post_tags")
        return tuple(t for t in tags if isinstance(t, str)) if isinstance(tags, list) else ()

    @property
    def post_probability(self) -> float:
        value = self.meta.get("post_probability")
        return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0

    @property
    def notable(self) -> bool:
        return bool(self.meta.get("notable", self.kind in ("oneoff", "seasonal")))

    @property
    def is_public(self) -> bool:
        return self.visibility == "public"

    @property
    def is_active(self) -> bool:
        return self.status != "cancelled"

    def covers(self, at: datetime) -> bool:
        return self.starts_at <= at < self.ends_at

    @classmethod
    def from_planned(cls, event: PlannedEvent) -> EventView:
        return cls(
            id=None,
            kind=event.kind,
            title=event.title,
            starts_at=event.starts_at,
            ends_at=event.ends_at,
            busyness=event.busyness,
            source=event.source,
            source_key=event.source_key,
            generated_for=event.generated_for,
            location=event.location,
            description=event.description,
            mood=event.mood,
            participants=event.participants,
            meta=event.meta(),
        )

    @classmethod
    def from_row(cls, row: asyncpg.Record) -> EventView:
        meta = row["meta"]
        return cls(
            id=row["id"],
            kind=row["kind"],
            title=row["title"],
            starts_at=row["starts_at"],
            ends_at=row["ends_at"],
            busyness=int(row["busyness"]),
            source=row["source"],
            source_key=row["source_key"],
            generated_for=row["generated_for"],
            location=row["location"],
            description=row["description"],
            mood=row["mood"],
            visibility=row["visibility"],
            status=row["status"],
            user_id=row["user_id"],
            participants=tuple(row["participants"] or ()),
            post_id=row["post_id"],
            meta=meta if isinstance(meta, dict) else {},
            created_at=row["created_at"],
        )
