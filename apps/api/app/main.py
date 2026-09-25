"""FastAPI アプリケーション（アプリファクトリ）。

起動: `uvicorn app.main:app`（`app` は初回アクセス時に `create_app()` で生成される）
      または `uvicorn --factory app.main:create_app`
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

import httpx
import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.container import build_services
from app.core.config import Settings, get_settings
from app.core.db import Pool, create_pool
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from app.routers import chat, comments, conversations, health, memories
from app.services.embedding import EmbeddingClient
from app.services.llm import LLMClient
from app.services.persona import PersonaRepository
from app.services.prompt import PromptBuilder

logger = get_logger("main")

API_TITLE: Final[str] = "everkano API"
API_DESCRIPTION: Final[str] = (
    "Project P MVP の Python API。DM のキャラ返答生成（メモリエンジン・Gate #1 モデレーション・監査ログ）、"
    "コメント投稿とキャラの返信生成、メモリパネル用の記憶 CRUD を提供する。\n\n"
    "認証: `Authorization: Bearer <Supabase access token>`（`GET /health` を除く）。"
    'エラーは `{"error": {"code", "message", "request_id"}}`（message は日本語）。'
)


def create_app(
    settings: Settings | None = None,
    *,
    llm: LLMClient | None = None,
    embedder: EmbeddingClient | None = None,
) -> FastAPI:
    """アプリを生成する。テストでは settings / llm / embedder を差し替えられる。"""
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.app_env,
            release=f"everkano-api@{__version__}",
            send_default_pii=False,
            traces_sample_rate=0.0,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # ペルソナ・テンプレートは起動時に検証（不正なら起動しない）
        personas = PersonaRepository.load_dir(settings.resolved_personas_dir)
        prompts = PromptBuilder.load_dir(settings.resolved_prompts_dir)
        http = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.llm_timeout_seconds),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
        try:
            pool = await create_pool(settings)
        except BaseException:
            await http.aclose()
            raise
        try:
            services = build_services(
                settings=settings,
                pool=pool,
                http=http,
                personas=personas,
                prompts=prompts,
                llm=llm,
                embedder=embedder,
            )
            app.state.services = services
            await _log_persona_coverage(services.pool, personas)
            logger.info(
                "startup complete",
                extra={
                    "fields": {
                        "version": __version__,
                        "env": settings.app_env,
                        "llm_mode": settings.llm_mode,
                        "llm_model": services.llm.model_name,
                        "embedding_mode": settings.embedding_mode,
                        "personas": len(personas),
                        "personas_dir": str(settings.resolved_personas_dir),
                        "prompts_dir": str(settings.resolved_prompts_dir),
                        "jwt_hs256_enabled": settings.supabase_jwt_secret is not None,
                    }
                },
            )
            yield
        finally:
            await pool.close()
            await http.aclose()
            logger.info("shutdown complete")

    app = FastAPI(
        title=API_TITLE,
        description=API_DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.app_env != "production" else None,
        redoc_url=None,
    )
    register_exception_handlers(app)
    app.add_middleware(RequestContextMiddleware, client_ip_header=settings.client_ip_header)
    # CORS は最外周（エラー応答にも CORS ヘッダを付けるため最後に追加）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER, "Retry-After"],
        max_age=600,
    )
    app.include_router(health.router)
    app.include_router(conversations.router)
    app.include_router(chat.router)
    app.include_router(memories.router)
    app.include_router(comments.router)
    return app


async def _log_persona_coverage(pool: Pool, personas: PersonaRepository) -> None:
    """有効キャラの persona_key に対応する YAML が無ければ警告する（system_prompt にフォールバック）。"""
    rows = await pool.fetch("select handle, persona_key from public.characters where is_active")
    missing = [f"{r['handle']}({r['persona_key']})" for r in rows if personas.get(r["persona_key"]) is None]
    if missing:
        logger.warning(
            "persona YAML missing for active characters; system_prompt fallback will be used",
            extra={"fields": {"characters": missing}},
        )


_app: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    """`uvicorn app.main:app` 用。import 時ではなく初回アクセス時にアプリを生成する
    （テスト等で app.main を import しても環境変数の検証が走らないようにするため）。"""
    global _app  # noqa: PLW0603
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
