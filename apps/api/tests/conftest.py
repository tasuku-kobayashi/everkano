"""pytest 共通フィクスチャ。

統合テスト（`@pytest.mark.integration`）はローカル Supabase の Postgres
（既定: postgresql://postgres:postgres@127.0.0.1:54322/postgres, `TEST_DATABASE_URL` で変更可）
に接続する。接続できない場合、ローカルでは skip する。
ただし `REQUIRE_TEST_DB=1`（未設定時は CI 上＝環境変数 `CI=true` なら有効）のときは skip せずに
テスト全体を失敗させる（所有者チェック等のセキュリティのテストが黙ってスキップされ、CI が緑になるのを防ぐ）。

テストは自前で auth.users / characters / posts を作成し、終了時にすべて削除する
（他の開発者が同じ DB を使っているため、db reset や DROP はしない）。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import jwt
import pytest

from app.container import EngineOverrides, Services
from app.core.config import Settings
from app.engine.types import Clock
from app.main import create_app
from app.services.embedding import EmbeddingClient
from app.services.llm import LLMClient

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
REPO_ROOT = TESTS_DIR.parents[2]
PROMPTS_DIR = REPO_ROOT / "packages" / "prompts" / "templates"

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
TEST_SUPABASE_URL = "http://127.0.0.1:54321"
TEST_ISSUER = f"{TEST_SUPABASE_URL}/auth/v1"
TEST_JWT_SECRET = "everkano-api-test-secret-0123456789abcdef"
TEST_PERSONA_KEY = "test_persona"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "local",
        "log_level": "WARNING",
        "database_url": DATABASE_URL,
        "database_pool_min_size": 1,
        "database_pool_max_size": 5,
        "supabase_url": TEST_SUPABASE_URL,
        "supabase_jwt_secret": TEST_JWT_SECRET,
        "llm_mode": "mock",
        "embedding_mode": "hash",
        "personas_dir": FIXTURES_DIR / "personas",
        "prompts_dir": PROMPTS_DIR,
        "rate_limit_chat_per_minute": 1000,
        "rate_limit_comments_per_minute": 1000,
        "comment_auto_reply_probability": 1.0,
        "audit_log_prompts": True,
        # テストではジョブのワーカー・スケジューラをプロセス内で常駐させない（同じ DB を他のテストが同時に使うため）。
        # 必要なテストは services.engine.worker.run_until_idle(now=...) / scheduler.run_due(now=...) で決定的に動かす
        "engine_worker_enabled": False,
        "engine_scheduler_enabled": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_token(
    user_id: uuid.UUID | str,
    *,
    email: str | None = None,
    secret: str = TEST_JWT_SECRET,
    audience: str = "authenticated",
    expires_in: int = 3600,
    issuer: str | None = TEST_ISSUER,
    extra: dict[str, Any] | None = None,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "aud": audience,
        "role": "authenticated",
        "email": email,
        "iat": now,
        "exp": now + expires_in,
    }
    if issuer is not None:
        claims["iss"] = issuer
    if extra:
        claims.update(extra)
    return jwt.encode(claims, secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# DB 接続可否
# ---------------------------------------------------------------------------

_db_available: bool | None = None


def _check_db() -> bool:
    global _db_available  # noqa: PLW0603
    if _db_available is None:

        async def probe() -> bool:
            try:
                conn = await asyncpg.connect(DATABASE_URL, timeout=3)
            except (OSError, asyncpg.PostgresError, TimeoutError):
                return False
            try:
                return bool(await conn.fetchval("select to_regclass('public.memories') is not null"))
            finally:
                await conn.close()

        _db_available = asyncio.run(probe())
    return _db_available


def db_required() -> bool:
    """DB に接続できないとき skip ではなく失敗にするか（REQUIRE_TEST_DB。未設定なら CI 上で有効）。"""
    flag = os.environ.get("REQUIRE_TEST_DB", "").strip().lower()
    if flag:
        return flag in {"1", "true", "yes"}
    return os.environ.get("CI", "").strip().lower() in {"1", "true"}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if any(item.get_closest_marker("integration") for item in items) and not _check_db():
        if db_required():
            pytest.exit(
                f"database not reachable (or schema not applied): {DATABASE_URL}. "
                "Integration tests are required here (REQUIRE_TEST_DB / CI); refusing to skip them.",
                returncode=1,
            )
        skip = pytest.mark.skip(reason=f"database not reachable: {DATABASE_URL}")
        for item in items:
            if item.get_closest_marker("integration"):
                item.add_marker(skip)


# ---------------------------------------------------------------------------
# テストデータ
# ---------------------------------------------------------------------------


@dataclass
class ApiUser:
    id: uuid.UUID
    email: str
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@dataclass
class World:
    """テストが作成した行を追跡し、最後にまとめて削除する。"""

    conn: asyncpg.Connection[asyncpg.Record]
    character_id: uuid.UUID = field(default_factory=uuid.uuid4)
    user_ids: list[uuid.UUID] = field(default_factory=list)
    character_ids: list[uuid.UUID] = field(default_factory=list)
    post_ids: list[uuid.UUID] = field(default_factory=list)

    async def create_character(self, *, persona_key: str = TEST_PERSONA_KEY, is_active: bool = True) -> uuid.UUID:
        character_id = uuid.uuid4()
        handle = f"t_{character_id.hex[:12]}"
        await self.conn.execute(
            """
            insert into public.characters (id, handle, name, avatar_url, bio, persona_key, system_prompt, is_active)
            values ($1, $2, 'テスト美咲', 'https://placehold.co/400x400', 'テスト用', $3,
                    'あなたはテスト用のキャラクターです。やさしく話します。', $4)
            """,
            character_id,
            handle,
            persona_key,
            is_active,
        )
        self.character_ids.append(character_id)
        return character_id

    async def create_user(self) -> ApiUser:
        user_id = uuid.uuid4()
        email = f"api-test-{user_id.hex[:12]}@example.test"
        await self.conn.execute(
            """
            insert into auth.users
              (instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
               raw_app_meta_data, raw_user_meta_data, created_at, updated_at)
            values ('00000000-0000-0000-0000-000000000000', $1, 'authenticated', 'authenticated', $2, '', now(),
                    '{"provider":"email","providers":["email"]}', '{}', now(), now())
            """,
            user_id,
            email,
        )
        self.user_ids.append(user_id)
        return ApiUser(id=user_id, email=email, token=make_token(user_id, email=email))

    async def create_post(
        self,
        *,
        character_id: uuid.UUID | None = None,
        caption: str = "今日はカフェで読書してた☕",
        published_at: datetime | None = None,
    ) -> uuid.UUID:
        post_id = uuid.uuid4()
        await self.conn.execute(
            """
            insert into public.posts (id, character_id, image_url, caption, published_at)
            values ($1, $2, 'https://placehold.co/1080x1080', $3, coalesce($4, now()))
            """,
            post_id,
            character_id or self.character_id,
            caption,
            published_at,
        )
        self.post_ids.append(post_id)
        return post_id

    async def cleanup(self) -> None:
        conn = self.conn
        if self.post_ids:
            await conn.execute("delete from public.comments where post_id = any($1::uuid[])", self.post_ids)
            await conn.execute(
                """
                delete from public.audit_logs
                 where event_type = 'comment.delete' and (payload->>'post_id')::uuid = any($1::uuid[])
                """,
                self.post_ids,
            )
        if self.character_ids:
            await conn.execute("delete from public.characters where id = any($1::uuid[])", self.character_ids)
        if self.user_ids:
            await conn.execute("delete from auth.users where id = any($1::uuid[])", self.user_ids)
        await conn.execute(
            "delete from public.audit_logs where user_id = any($1::uuid[]) or character_id = any($2::uuid[])",
            self.user_ids,
            self.character_ids,
        )
        # 返答の後の非同期ジョブ（post_turn など）はユーザー・キャラへの外部キーを持たないので payload で消す
        await conn.execute(
            """
            delete from public.engine_jobs
             where payload->>'user_id' = any($1::text[]) or payload->>'character_id' = any($2::text[])
            """,
            [str(u) for u in self.user_ids],
            [str(c) for c in self.character_ids],
        )


@pytest.fixture
async def db() -> AsyncIterator[asyncpg.Connection[asyncpg.Record]]:
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def world(db: asyncpg.Connection[asyncpg.Record]) -> AsyncIterator[World]:
    w = World(conn=db)
    try:
        w.character_id = await w.create_character()
        yield w
    finally:
        await w.cleanup()


AppFactory = Callable[..., Awaitable[httpx.AsyncClient]]


@pytest.fixture
async def app_factory() -> AsyncIterator[AppFactory]:
    """create_app → lifespan 起動 → httpx.AsyncClient(ASGITransport) を返すファクトリ。"""
    async with AsyncExitStack() as stack:

        async def make(
            settings: Settings | None = None,
            *,
            llm: LLMClient | None = None,
            embedder: EmbeddingClient | None = None,
            clock: Clock | None = None,
            engine_overrides: EngineOverrides | None = None,
        ) -> httpx.AsyncClient:
            app = create_app(
                settings or make_settings(), llm=llm, embedder=embedder, clock=clock, engine_overrides=engine_overrides
            )
            await stack.enter_async_context(app.router.lifespan_context(app))
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
            await stack.enter_async_context(client)
            client.app = app  # type: ignore[attr-defined]
            return client

        yield make


@pytest.fixture
async def client(app_factory: AppFactory) -> httpx.AsyncClient:
    return await app_factory()


def services_of(client: httpx.AsyncClient) -> Services:
    """app_factory で作ったクライアントのアプリのサービス群（ワーカー・スケジューラ・時計を直接動かす）。"""
    services: Services = client.app.state.services  # type: ignore[attr-defined]
    return services
