"""FastAPI アプリケーション（アプリファクトリ）。

起動: `uvicorn app.main:app`（`app` は初回アクセス時に `create_app()` で生成される）
      または `uvicorn --factory app.main:create_app`
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.container import EngineOverrides, Services, build_services
from app.core.config import Settings, get_settings
from app.core.db import Pool, create_pool
from app.core.errors import register_exception_handlers
from app.core.http import create_jwks_client, create_upstream_client
from app.core.logging import configure_logging, get_logger
from app.core.middleware import REQUEST_ID_HEADER, BodySizeLimitMiddleware, RequestContextMiddleware
from app.core.observability import init_sentry
from app.engine.types import Clock
from app.routers import chat, comments, conversations, health, memories, proactive, promises, safety
from app.services.embedding import EmbeddingClient
from app.services.llm import LLMClient
from app.services.persona import PersonaRepository
from app.services.prompt import PromptBuilder

logger = get_logger("main")

API_TITLE: Final[str] = "everkano API"
API_DESCRIPTION: Final[str] = (
    "Project P の Python API。DM のキャラ返答生成（キャラクターエンジン v1.0: 記憶 × カレンダー × 好感度・"
    "ストリーミング・E6 の安全対応・Gate #1 モデレーション・監査ログ）、コメント投稿とキャラの返信生成、"
    "メモリパネル用の記憶・約束の API、自発メッセージの設定を提供する。\n\n"
    "認証: `Authorization: Bearer <Supabase access token>`（`GET /health` を除く）。"
    'エラーは `{"error": {"code", "message", "request_id"}}`（message は日本語）。'
)


def create_app(
    settings: Settings | None = None,
    *,
    llm: LLMClient | None = None,
    embedder: EmbeddingClient | None = None,
    clock: Clock | None = None,
    engine_overrides: EngineOverrides | None = None,
) -> FastAPI:
    """アプリを生成する。テスト・評価ハーネスでは settings / llm / embedder / 時計 / モジュールを差し替えられる。

    ENGINE_WORKER_ENABLED / ENGINE_SCHEDULER_ENABLED が true なら、ジョブのワーカーとスケジューラをこのプロセス内で
    動かす（本番は API では無効にし、Fly.io の worker プロセスグループ `python -m app.worker` で動かす）。
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    # 本文・トークン・ローカル変数・監査ログを送らない設定で初期化する（app/core/observability.py）
    init_sentry(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # ペルソナ・テンプレートは起動時に検証（不正なら起動しない）
        personas = PersonaRepository.load_dir(settings.resolved_personas_dir)
        prompts = PromptBuilder.load_dir(settings.resolved_prompts_dir)
        # LLM・埋め込み用（接続数は fly.toml の同時リクエスト上限 × 2 以上）と JWKS 用は別クライアント
        # （app/core/http.py）
        http = create_upstream_client(settings.llm_timeout_seconds)
        jwks_http = create_jwks_client()
        try:
            pool = await create_pool(settings)
        except BaseException:
            await http.aclose()
            await jwks_http.aclose()
            raise
        try:
            services = build_services(
                settings=settings,
                pool=pool,
                http=http,
                jwks_http=jwks_http,
                personas=personas,
                prompts=prompts,
                llm=llm,
                embedder=embedder,
                clock=clock,
                engine_overrides=engine_overrides,
            )
            app.state.services = services
            await _log_persona_coverage(services.pool, personas)
            # 起動直後の最初のリクエストが JWKS の取得を待たないよう先に取得しておく（失敗しても起動は続ける）
            await services.jwks.warm_up()
            start_engine_background(services)
            logger.info(
                "startup complete",
                extra={
                    "fields": {
                        "version": __version__,
                        "env": settings.app_env,
                        "llm_mode": settings.llm_mode,
                        "llm_model": services.llm.model_name,
                        "embedding_mode": settings.embedding_mode,
                        # DB 接続の TLS（接続文字列そのものはパスワードを含むので出さない）
                        "database_sslmode": settings.database_sslmode or "prefer (default)",
                        "personas": len(personas),
                        "personas_dir": str(settings.resolved_personas_dir),
                        "prompts_dir": str(settings.resolved_prompts_dir),
                        "jwt_hs256_enabled": settings.supabase_jwt_secret is not None,
                        "engine": {
                            "memory": settings.engine_memory_enabled,
                            "calendar": settings.engine_calendar_enabled,
                            "affinity": settings.engine_affinity_enabled,
                            "proactive": settings.engine_proactive_enabled,
                            "worker": settings.engine_worker_enabled,
                            "scheduler": settings.engine_scheduler_enabled,
                        },
                    }
                },
            )
            yield
        finally:
            services_or_none: Services | None = getattr(app.state, "services", None)
            if services_or_none is not None:
                await stop_engine_background(services_or_none)
            await pool.close()
            await http.aclose()
            await jwks_http.aclose()
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
    # 後に追加したものほど外側。外 → 内: CORS → RequestContext → BodySizeLimit → ルーター
    # 本文の上限は認証より前（FastAPI は依存関係の解決前に本文を読む）に効かせる。413 にも X-Request-ID と CORS が付く
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    app.add_middleware(RequestContextMiddleware, client_ip_header=settings.client_ip_header)
    # CORS は最外周（エラー応答にも CORS ヘッダを付けるため最後に追加）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER, "Retry-After"],
        max_age=600,
    )
    app.include_router(health.router)
    app.include_router(conversations.router)
    app.include_router(chat.router)
    app.include_router(memories.router)
    app.include_router(promises.router)
    app.include_router(proactive.router)
    app.include_router(safety.router)
    app.include_router(comments.router)
    return app


def start_engine_background(services: Services) -> None:
    """設定に応じて、ジョブのワーカーとスケジューラをこのプロセス内で起動する。"""
    settings = services.settings
    if settings.engine_worker_enabled:
        services.engine.worker.start()
    if settings.engine_scheduler_enabled:
        services.engine.scheduler.start()


async def stop_engine_background(services: Services) -> None:
    """停止時: スケジューラ・ワーカーを止め、生成中の返答（切断されたものを含む）の保存を待つ。"""
    await services.engine.scheduler.stop()
    await services.engine.worker.stop()
    await services.chat.drain()


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
