"""Calendar Engine のテスト用フィクスチャ（統合テストはローカル Supabase の Postgres を使う）。

- 予定を作るキャラはテストごとに新しく作り、終了時に削除する（予定・状態・記憶・投稿は外部キーの cascade で消える）。
- 共有 DB の既存キャラ（シードの 10 体）には触れない: ensure_schedules / run_tick / check_consistency には
  必ず character_ids を渡す。
- テストの時刻は 2027 年（他の開発者が動かすスケジューラの「今」と重ならないように）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from app.core.db import Pool, create_pool
from app.engine.calendar.service import CalendarConfig, CalendarEngine
from app.engine.types import OutputGuard
from app.services.audit import AuditLogger
from app.services.embedding import HashEmbedding
from app.services.llm import LLMClient, MockLLM
from app.services.moderation import Moderator
from app.services.persona import Persona, PersonaRepository
from tests.conftest import World, make_settings
from tests.engine.calendar.personas import all_fixture_personas


@pytest.fixture
async def pool() -> AsyncIterator[Pool]:
    p = await create_pool(make_settings(database_pool_max_size=5))
    try:
        yield p
    finally:
        await p.close()


@dataclass
class CalendarWorld:
    """テスト用キャラの作成と、エンジンの組み立て。"""

    world: World
    pool: Pool

    async def character(self, persona: Persona, *, name: str | None = None) -> uuid.UUID:
        character_id = uuid.uuid4()
        await self.world.conn.execute(
            """
            insert into public.characters (id, handle, name, avatar_url, bio, persona_key, system_prompt)
            values ($1, $2, $3, 'https://placehold.co/400x400', 'テスト用', $4, 'テスト用のキャラクターです。')
            """,
            character_id,
            f"c_{character_id.hex[:12]}",
            name or persona.name,
            persona.key,
        )
        self.world.character_ids.append(character_id)
        return character_id

    def engine(
        self,
        *,
        personas: Sequence[Persona] | None = None,
        llm: LLMClient | None = None,
        guard: OutputGuard | None = None,
        config: CalendarConfig | None = None,
    ) -> CalendarEngine:
        return CalendarEngine(
            pool=self.pool,
            llm=llm or MockLLM(),
            audit=AuditLogger(self.pool),
            personas=PersonaRepository(personas if personas is not None else all_fixture_personas()),
            moderator=Moderator(),
            output_guard=guard,
            embedder=HashEmbedding(),
            config=config,
        )

    async def audits(self, character_id: uuid.UUID, event_type: str) -> list[dict[str, Any]]:
        rows = await self.world.conn.fetch(
            "select payload from public.audit_logs where character_id = $1 and event_type = $2 order by id",
            character_id,
            event_type,
        )
        return [dict(r["payload"]) for r in rows]


@pytest.fixture
async def cal(world: World, pool: Pool) -> CalendarWorld:
    return CalendarWorld(world=world, pool=pool)


EngineFactory = Callable[..., CalendarEngine]
