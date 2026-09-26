"""自発メッセージのテストの補助（他のモジュールの Protocol の偽物・状態・世界の時計）。"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.engine.types import (
    CharacterStateSnapshot,
    GuardResult,
    MemoryContext,
    PromiseItem,
    WorldState,
    to_jst,
)

_WEEKDAYS = ("月", "火", "水", "木", "金", "土", "日")


def snapshot(
    activity: str = "自宅でのんびり",
    *,
    busyness: int = 0,
    event_id: uuid.UUID | None = None,
    event_kind: str | None = None,
    location: str | None = "自宅",
) -> CharacterStateSnapshot:
    return CharacterStateSnapshot(
        activity=activity,
        location=location,
        mood=None,
        busyness=busyness,
        status_label=activity[:20],
        event_id=event_id,
        event_kind=event_kind,
        reply_style_hint="ふつうに返信できる。",
    )


def world_at(now: datetime, seasonal: Sequence[tuple[str, str]] = ()) -> WorldState:
    local = to_jst(now)
    return WorldState(
        now=now,
        now_jst=local,
        weekday_ja=_WEEKDAYS[local.weekday()],
        season="autumn",
        season_ja="秋",
        time_of_day_ja="朝" if local.hour < 11 else "夜",
        holiday_name=None,
        is_day_off=local.weekday() >= 5,
        seasonal_keys=tuple(k for k, _ in seasonal),
        seasonal_labels_ja=tuple(label for _, label in seasonal),
    )


StateFn = Callable[[uuid.UUID, datetime], CharacterStateSnapshot]


@dataclass
class FakeCalendar:
    """CalendarService の偽物。state_fn(character_id, now) で任意の時刻の状態を返す。"""

    state_fn: StateFn = lambda _cid, _now: snapshot()
    seasonal: Sequence[tuple[str, str]] = ()
    calls: list[tuple[uuid.UUID, datetime]] = field(default_factory=list)

    def world_state(self, now: datetime) -> WorldState:
        return world_at(now, self.seasonal)

    async def current_state(self, *, character_id: uuid.UUID, now: datetime) -> CharacterStateSnapshot:
        self.calls.append((character_id, now))
        return self.state_fn(character_id, now)

    async def ensure_schedules(self, *, now: datetime, days_ahead: int) -> int:
        return 0

    async def tick(self, *, now: datetime) -> None:
        return None

    async def sync_promise_events(
        self, *, user_id: uuid.UUID, character_id: uuid.UUID, promise_ids: Sequence[uuid.UUID], now: datetime
    ) -> None:
        return None


def event_then_home(event_id: uuid.UUID, ended_at: datetime, *, title: str = "同期と飲み会") -> StateFn:
    """ended_at までは単発の予定（飲み会）、その後は自宅。"""

    def state(_cid: uuid.UUID, now: datetime) -> CharacterStateSnapshot:
        if ended_at - timedelta(hours=3) <= now < ended_at:
            return snapshot(title, busyness=2, event_id=event_id, event_kind="oneoff", location="新宿の居酒屋")
        return snapshot("帰宅してのんびり", busyness=1)

    return state


@dataclass
class FakeMemory:
    """MemoryService の偽物（約束の期日と、話題にした印だけ）。"""

    promises: dict[tuple[uuid.UUID, uuid.UUID], list[PromiseItem]] = field(default_factory=dict)
    mentioned: list[uuid.UUID] = field(default_factory=list)

    def add_promise(
        self,
        user_id: uuid.UUID,
        character_id: uuid.UUID,
        content: str,
        due_at: datetime,
        *,
        precision: str = "day",
    ) -> PromiseItem:
        item = PromiseItem(id=uuid.uuid4(), content=content, due_at=due_at, due_precision=precision, status="pending")
        self.promises.setdefault((user_id, character_id), []).append(item)
        return item

    async def retrieve_context(
        self,
        *,
        user_id: uuid.UUID,
        character_id: uuid.UUID,
        query_text: str,
        query_embedding: list[float] | None,
        now: datetime,
    ) -> MemoryContext:
        return MemoryContext(memories=(), character_memories=(), promises=())

    async def embed_query(
        self, text: str, *, user_id: uuid.UUID, character_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> list[float] | None:
        return None

    async def mark_referenced(self, *, memory_ids: Sequence[uuid.UUID], now: datetime) -> None:
        return None

    async def process_turns(self, **_: object) -> object:  # pragma: no cover - 使わない
        raise NotImplementedError

    async def maybe_summarize(
        self, *, conversation_id: uuid.UUID, user_id: uuid.UUID, character_id: uuid.UUID, now: datetime
    ) -> None:
        return None

    async def due_promises(
        self, *, user_id: uuid.UUID, character_id: uuid.UUID, now: datetime, window: timedelta
    ) -> Sequence[PromiseItem]:
        items = self.promises.get((user_id, character_id), [])
        return [
            p
            for p in items
            if p.due_at is not None and abs(p.due_at - now) <= window + timedelta(days=1) and p.id not in self.mentioned
        ]

    async def mark_promise_mentioned(self, *, promise_id: uuid.UUID, now: datetime) -> None:
        self.mentioned.append(promise_id)


@dataclass
class FakeGuard:
    """OutputGuard の偽物（指定した語を含む文を差し止める）。"""

    words: tuple[str, ...] = ()
    checked: list[str] = field(default_factory=list)

    def check(self, text: str) -> GuardResult:
        self.checked.append(text)
        hits = tuple(w for w in self.words if w in text)
        return GuardResult(flagged=bool(hits), categories=("commerce_coupling",) if hits else (), matched=hits)
