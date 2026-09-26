"""Memory Engine のテスト用フィクスチャ。

統合テスト（DB）は tests/conftest.py の `world`（テスト用のユーザー・キャラを作り、最後に削除する）を使う。
サービスは create_app を通さずに直接組み立てる（コアの配線と独立に検証するため）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest

from app.core.db import Pool, create_pool
from app.engine.memory import MemoryConfig, MemoryEngineService
from app.engine.memory.embedding import EmbeddingClient, HashEmbedding
from app.engine.types import TurnRecord
from app.main import create_app
from app.routers import promises as promises_router
from app.services.audit import AuditLogger
from app.services.llm import LLMClient, MockLLM
from app.services.moderation import Moderator
from app.services.persona import PersonaRepository
from tests.conftest import FIXTURES_DIR, PROMPTS_DIR, World, make_settings


@pytest.fixture
async def pool() -> AsyncIterator[Pool]:
    created = await create_pool(make_settings())
    try:
        yield created
    finally:
        await created.close()


def make_config(**overrides: Any) -> MemoryConfig:
    return MemoryConfig(prompts_dir=PROMPTS_DIR, **overrides)


def make_service(
    pool: Pool,
    *,
    llm: LLMClient | None = None,
    embedder: EmbeddingClient | None = None,
    **config: Any,
) -> MemoryEngineService:
    return MemoryEngineService(
        pool=pool,
        llm=llm or MockLLM(),
        embedder=embedder or HashEmbedding(1536),
        audit=AuditLogger(pool),
        personas=PersonaRepository.load_dir(FIXTURES_DIR / "personas"),
        moderator=Moderator(),
        config=make_config(**config),
    )


@dataclass
class Pair:
    """テスト用のユーザー × キャラと会話。ターンを DB に保存して TurnRecord を作る。"""

    world: World
    user_id: uuid.UUID
    character_id: uuid.UUID
    conversation_id: uuid.UUID
    headers: dict[str, str]
    turns: list[TurnRecord] = field(default_factory=list)

    async def turn(self, user_text: str, reply_text: str = "うんうん、それで？", *, at: datetime) -> TurnRecord:
        conn = self.world.conn
        user_message_id = await conn.fetchval(
            "insert into public.messages (conversation_id, sender_type, body, created_at)"
            " values ($1, 'user', $2, $3) returning id",
            self.conversation_id,
            user_text,
            at,
        )
        character_message_id = await conn.fetchval(
            "insert into public.messages (conversation_id, sender_type, body, created_at)"
            " values ($1, 'character', $2, $3) returning id",
            self.conversation_id,
            reply_text,
            at + timedelta(seconds=1),
        )
        record = TurnRecord(
            conversation_id=self.conversation_id,
            user_id=self.user_id,
            character_id=self.character_id,
            user_message_id=user_message_id,
            character_message_id=character_message_id,
            user_text=user_text,
            reply_text=reply_text,
            occurred_at=at,
        )
        self.turns.append(record)
        return record

    async def memories(self, *, include_superseded: bool = True) -> list[dict[str, Any]]:
        rows = await self.world.conn.fetch(
            """
            select id, kind, content, importance, tags, status, superseded_by, superseded_at, is_user_edited,
                   source_message_id, source_conversation_id, created_at, updated_at, last_referenced_at,
                   reference_count
              from public.memories
             where user_id = $1 and character_id = $2 and ($3 or status = 'active')
             order by created_at, id
            """,
            self.user_id,
            self.character_id,
            include_superseded,
        )
        return [dict(r) for r in rows]

    async def promises(self) -> list[dict[str, Any]]:
        rows = await self.world.conn.fetch(
            "select * from public.promises where user_id = $1 and character_id = $2 order by created_at, id",
            self.user_id,
            self.character_id,
        )
        return [dict(r) for r in rows]

    async def audit(self, event_type: str) -> list[dict[str, Any]]:
        rows = await self.world.conn.fetch(
            "select payload from public.audit_logs where user_id = $1 and event_type = $2 order by id",
            self.user_id,
            event_type,
        )
        return [dict(r["payload"]) for r in rows]


async def make_pair(world: World, character_id: uuid.UUID | None = None) -> Pair:
    user = await world.create_user()
    character = character_id or world.character_id
    conversation_id = await world.conn.fetchval(
        "insert into public.conversations (user_id, character_id) values ($1, $2) returning id",
        user.id,
        character,
    )
    return Pair(
        world=world,
        user_id=user.id,
        character_id=character,
        conversation_id=conversation_id,
        headers=user.headers,
    )


@pytest.fixture
async def pair(world: World) -> Pair:
    return await make_pair(world)


async def process(service: MemoryEngineService, pair: Pair, turns: Sequence[TurnRecord], now: datetime) -> Any:
    return await service.process_turns(
        user_id=pair.user_id,
        character_id=pair.character_id,
        conversation_id=pair.conversation_id,
        turns=turns,
        now=now,
    )


@pytest.fixture
async def api_client() -> AsyncIterator[httpx.AsyncClient]:
    """create_app + promises ルーター（コアが main.py に配線するまではテスト側で足す）。"""
    async with AsyncExitStack() as stack:
        app = create_app(make_settings(rate_limit_memories_per_minute=1000))
        if not any(getattr(route, "path", "") == "/promises" for route in app.routes):
            app.include_router(promises_router.router)
        await stack.enter_async_context(app.router.lifespan_context(app))
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
        await stack.enter_async_context(client)
        yield client
