"""コア（ジョブ・スケジューラ・Context Assembler・パイプライン）のテスト用フィクスチャ。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

import pytest

from app.core.db import Pool, create_pool
from app.engine.context_assembler import basic_world_state
from app.engine.types import (
    AffinityUpdateResult,
    CharacterStateSnapshot,
    MemoryContext,
    MemoryProcessResult,
    PromiseItem,
    RelationshipGuidance,
    TurnRecord,
    WorldState,
)
from app.services.audit import AuditLogger
from tests.conftest import make_settings


@pytest.fixture
async def pool() -> AsyncIterator[Pool]:
    created = await create_pool(make_settings())
    try:
        yield created
    finally:
        await created.close()


@pytest.fixture
def audit(pool: Pool) -> AuditLogger:
    return AuditLogger(pool)


# ---------------------------------------------------------------------------
# モジュールの差し替え（Protocol どおりのフェイク。呼び出しを記録する）
# ---------------------------------------------------------------------------


@dataclass
class FakeMemory:
    context: MemoryContext = field(default_factory=lambda: MemoryContext((), (), ()))
    fail_process: int = 0  # process_turns を何回失敗させるか
    delay: float = 0.0
    raise_on_retrieve: bool = False
    processed: list[tuple[TurnRecord, ...]] = field(default_factory=list)
    promises: tuple[UUID, ...] = ()
    summarized: list[UUID] = field(default_factory=list)
    referenced: list[tuple[UUID, ...]] = field(default_factory=list)

    async def retrieve_context(
        self,
        *,
        user_id: UUID,
        character_id: UUID,
        query_text: str,
        query_embedding: list[float] | None,
        now: datetime,
    ) -> MemoryContext:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_on_retrieve:
            raise RuntimeError("memory store down")
        return self.context

    async def embed_query(
        self, text: str, *, user_id: UUID, character_id: UUID, conversation_id: UUID
    ) -> list[float] | None:
        return [0.0] * 3

    async def mark_referenced(self, *, memory_ids: Sequence[UUID], now: datetime) -> None:
        self.referenced.append(tuple(memory_ids))

    async def process_turns(
        self,
        *,
        user_id: UUID,
        character_id: UUID,
        conversation_id: UUID,
        turns: Sequence[TurnRecord],
        now: datetime,
    ) -> MemoryProcessResult:
        if self.fail_process > 0:
            self.fail_process -= 1
            raise RuntimeError("memory analysis failed")
        self.processed.append(tuple(turns))
        return MemoryProcessResult(promises_created=self.promises)

    async def maybe_summarize(self, *, conversation_id: UUID, user_id: UUID, character_id: UUID, now: datetime) -> None:
        self.summarized.append(conversation_id)

    async def due_promises(
        self, *, user_id: UUID, character_id: UUID, now: datetime, window: timedelta
    ) -> Sequence[PromiseItem]:
        return ()

    async def mark_promise_mentioned(self, *, promise_id: UUID, now: datetime) -> None:
        return None


@dataclass
class FakeCalendar:
    state: CharacterStateSnapshot | None = None
    fail: bool = False
    synced: list[tuple[UUID, ...]] = field(default_factory=list)

    def world_state(self, now: datetime) -> WorldState:
        return basic_world_state(now)

    async def current_state(self, *, character_id: UUID, now: datetime) -> CharacterStateSnapshot:
        if self.fail or self.state is None:
            raise RuntimeError("calendar down")
        return self.state

    async def ensure_schedules(self, *, now: datetime, days_ahead: int) -> int:
        return 0

    async def tick(self, *, now: datetime) -> None:
        return None

    async def sync_promise_events(
        self, *, user_id: UUID, character_id: UUID, promise_ids: Sequence[UUID], now: datetime
    ) -> None:
        if self.fail:
            raise RuntimeError("calendar down")
        self.synced.append(tuple(promise_ids))


@dataclass
class FakeAffinity:
    guidance_value: RelationshipGuidance | None = None
    fail_evaluate: int = 0
    evaluated: list[tuple[TurnRecord, ...]] = field(default_factory=list)
    touched: list[datetime] = field(default_factory=list)

    async def guidance(self, *, user_id: UUID, character_id: UUID, now: datetime) -> RelationshipGuidance:
        if self.guidance_value is None:
            raise RuntimeError("affinity down")
        return self.guidance_value

    async def touch_interaction(self, *, user_id: UUID, character_id: UUID, now: datetime) -> None:
        self.touched.append(now)

    async def evaluate_turns(
        self, *, user_id: UUID, character_id: UUID, turns: Sequence[TurnRecord], now: datetime
    ) -> AffinityUpdateResult:
        if self.fail_evaluate > 0:
            self.fail_evaluate -= 1
            raise RuntimeError("affinity eval failed")
        self.evaluated.append(tuple(turns))
        return AffinityUpdateResult()

    async def apply_daily_maintenance(self, *, now: datetime) -> int:
        return 0


@dataclass
class FakeProactive:
    replied: list[UUID] = field(default_factory=list)
    scans: list[datetime] = field(default_factory=list)

    async def scan(self, *, now: datetime) -> int:
        self.scans.append(now)
        return 0

    async def on_user_message(self, *, user_id: UUID, character_id: UUID, conversation_id: UUID, now: datetime) -> None:
        self.replied.append(conversation_id)
