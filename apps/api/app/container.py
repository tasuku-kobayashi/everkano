"""アプリ全体で共有するサービス群（lifespan で生成し `app.state.services` に保持）。

キャラクターエンジン v1.0 の配線（ENGINE_BRIEF §2.1）:
- 時計（Clock）は 1 つをコンテナが持つ（本番は SystemClock。テスト・評価ハーネスは ManualClock を渡す）。
  パイプライン・ジョブ・スケジューラはこの時計から `now` を取り、各モジュールのメソッドに渡す。
- 各モジュール（記憶・カレンダー・好感度・自発メッセージ）はここで生成し、Protocol（app/engine/types.py）として
  Context Assembler・ジョブのハンドラ・パイプラインに渡す。ENGINE_*_ENABLED が false のモジュールは渡さない
  （評価ハーネスの「素の LLM」）。テストはモジュールを差し替えられる（`EngineOverrides`）。
- OutputGuard（E2 / E3）と安全対応（E6）は、チャット・自発メッセージ・フィードのキャプションで共有する。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

import httpx
from fastapi import Depends, Request

from app.core.config import Settings
from app.core.db import Pool
from app.core.errors import ApiError
from app.core.logging import get_logger
from app.core.security import CurrentUser, CurrentUserDep, JwksCache, TokenVerifier
from app.engine.affinity.config import AffinityConfig
from app.engine.affinity.service import AffinityEngine
from app.engine.calendar import CalendarConfig, CalendarEngine
from app.engine.context_assembler import ContextAssembler, ModuleFlags
from app.engine.jobs import EngineJobHandlers, JobRegistry, PgJobQueue, Worker
from app.engine.memory import MemoryConfig, MemoryEngineService
from app.engine.pipeline import ChatPipeline, PipelineModules
from app.engine.proactive.config import ProactiveConfig
from app.engine.proactive.service import ProactiveMessenger
from app.engine.safety import DefaultOutputGuard, DefaultSafetyService, load_safety_config
from app.engine.scheduler import EngineScope, PeriodicTask, Scheduler, daily_at_jst, every
from app.engine.types import (
    AffinityService,
    CalendarService,
    Clock,
    MemoryService,
    OutputGuard,
    ProactiveService,
    SystemClock,
)
from app.services.audit import AuditLogger
from app.services.chat import ChatService
from app.services.comments import CommentService
from app.services.conversations import ConversationService
from app.services.embedding import EmbeddingClient, create_embedding_client
from app.services.llm import LLMClient, create_llm_client
from app.services.moderation import Moderator
from app.services.persona import PersonaRepository
from app.services.prompt import PromptBuilder
from app.services.rate_limit import SlidingWindowRateLimiter
from app.services.user_memories import UserMemoryService

logger = get_logger("container")

# 1日1回のタスクの時刻（JST）
JOBS_CLEANUP_HOUR_JST = 3


@dataclass(frozen=True, slots=True)
class EngineOverrides:
    """テスト・評価ハーネス用: モジュールを差し替える（None の項目は通常どおり生成する）。"""

    memory: MemoryService | None = None
    calendar: CalendarService | None = None
    affinity: AffinityService | None = None
    proactive: ProactiveService | None = None


@dataclass(frozen=True, slots=True)
class EngineServices:
    """キャラクターエンジンの部品（評価ハーネスはこれを直接使って時間を早送りする）。"""

    clock: Clock
    # 定期実行の対象の絞り込み（評価ハーネス・テスト用。本番は空 = 全員）
    scope: EngineScope
    safety: DefaultSafetyService
    guard: OutputGuard
    jobs: PgJobQueue
    registry: JobRegistry
    handlers: EngineJobHandlers
    worker: Worker
    scheduler: Scheduler
    assembler: ContextAssembler
    pipeline: ChatPipeline
    # 生成したモジュール（ENGINE_*_ENABLED に関係なく生成する。無効なものはパイプライン等に渡していない）
    memory: MemoryService | None
    calendar: CalendarService | None
    affinity: AffinityService | None
    proactive: ProactiveService | None


@dataclass(frozen=True, slots=True)
class Services:
    settings: Settings
    pool: Pool
    http: httpx.AsyncClient
    jwks: JwksCache
    token_verifier: TokenVerifier
    personas: PersonaRepository
    prompts: PromptBuilder
    llm: LLMClient
    embedder: EmbeddingClient
    moderator: Moderator
    audit: AuditLogger
    rate_limiter: SlidingWindowRateLimiter
    clock: Clock
    engine: EngineServices
    chat: ChatService
    conversations: ConversationService
    user_memories: UserMemoryService
    comments: CommentService


# ---------------------------------------------------------------------------
# モジュールの生成（各モジュールのファサード。app/engine/<module>/）
# ---------------------------------------------------------------------------


def _build_memory(
    settings: Settings,
    *,
    pool: Pool,
    llm: LLMClient,
    embedder: EmbeddingClient,
    audit: AuditLogger,
    personas: PersonaRepository,
    moderator: Moderator,
) -> MemoryService:
    return MemoryEngineService(
        pool=pool,
        llm=llm,
        embedder=embedder,
        audit=audit,
        personas=personas,
        moderator=moderator,
        config=MemoryConfig.from_settings(settings),
    )


def _build_calendar(
    settings: Settings,
    *,
    pool: Pool,
    llm: LLMClient,
    embedder: EmbeddingClient,
    audit: AuditLogger,
    personas: PersonaRepository,
    moderator: Moderator,
    guard: OutputGuard,
) -> CalendarService:
    return CalendarEngine(
        pool=pool,
        llm=llm,
        audit=audit,
        personas=personas,
        moderator=moderator,
        output_guard=guard,
        embedder=embedder,
        config=CalendarConfig(
            caption_model=settings.llm_model_for("feed_caption"),
            prompts_dir=settings.resolved_prompts_dir,
        ),
    )


def _build_affinity(
    settings: Settings, *, pool: Pool, llm: LLMClient, audit: AuditLogger, personas: PersonaRepository
) -> AffinityService:
    return AffinityEngine(
        pool=pool,
        llm=llm,
        audit=audit,
        personas=personas,
        prompts_dir=settings.resolved_prompts_dir,
        config=AffinityConfig(model=settings.llm_model_for("affinity_eval")),
    )


def _build_proactive(
    settings: Settings,
    *,
    pool: Pool,
    llm: LLMClient,
    audit: AuditLogger,
    personas: PersonaRepository,
    moderator: Moderator,
    guard: OutputGuard,
    calendar: CalendarService | None,
    memory: MemoryService | None,
    affinity: AffinityService | None,
) -> ProactiveService:
    return ProactiveMessenger(
        pool=pool,
        llm=llm,
        audit=audit,
        personas=personas,
        moderator=moderator,
        guard=guard,
        prompts_dir=settings.resolved_prompts_dir,
        calendar=calendar,
        memory=memory,
        affinity=affinity,
        config=ProactiveConfig(
            model=settings.llm_model_for("proactive_message"),
            per_user_daily_limit=settings.engine_proactive_daily_limit,
            quiet_start_default=settings.engine_proactive_quiet_start,
            quiet_end_default=settings.engine_proactive_quiet_end,
        ),
    )


def periodic_tasks(
    settings: Settings,
    *,
    jobs: PgJobQueue,
    calendar: CalendarService | None,
    affinity: AffinityService | None,
    proactive: ProactiveService | None,
    scope: EngineScope | None = None,
) -> list[PeriodicTask]:
    """スケジューラのタスク（ENGINE_BRIEF §2.3）。無効なモジュールのタスクは登録しない。

    scope（評価ハーネス・テスト用）に対象のキャラ・ユーザーが入っていれば、各モジュールの絞り込み付きのメソッドを使う
    （Protocol に無い引数なので、実装クラスの場合だけ渡す。フェイクのモジュールでは全体の処理を呼ぶ）。
    """
    scope = scope or EngineScope()
    tasks: list[PeriodicTask] = []
    if calendar is not None:
        cal = calendar
        days_ahead = settings.engine_calendar_days_ahead

        async def ensure_schedules(now: datetime) -> int:
            if isinstance(cal, CalendarEngine) and scope.character_ids is not None:
                return await cal.ensure_schedules(now=now, days_ahead=days_ahead, character_ids=scope.character_ids)
            return await cal.ensure_schedules(now=now, days_ahead=days_ahead)

        async def tick(now: datetime) -> object:
            if isinstance(cal, CalendarEngine):
                return await cal.run_tick(now=now, character_ids=scope.character_ids)
            await cal.tick(now=now)
            return None

        tasks.append(
            PeriodicTask(
                "calendar.ensure_schedules",
                ensure_schedules,
                every(timedelta(seconds=settings.engine_calendar_ensure_interval_seconds)),
            )
        )
        tick_interval = timedelta(seconds=settings.engine_calendar_tick_interval_seconds)
        tasks.append(PeriodicTask("calendar.tick", tick, every(tick_interval)))
    if proactive is not None:
        messenger = proactive

        async def scan(now: datetime) -> int:
            if isinstance(messenger, ProactiveMessenger) and scope.user_ids is not None:
                return await messenger.scan(now=now, user_ids=scope.user_ids)
            return await messenger.scan(now=now)

        tasks.append(
            PeriodicTask(
                "proactive.scan", scan, every(timedelta(seconds=settings.engine_proactive_scan_interval_seconds))
            )
        )
    if affinity is not None:
        engine = affinity

        async def daily(now: datetime) -> int:
            if isinstance(engine, AffinityEngine) and scope.user_ids is not None:
                return await engine.apply_daily_maintenance(now=now, user_ids=scope.user_ids)
            return await engine.apply_daily_maintenance(now=now)

        tasks.append(
            PeriodicTask(
                "affinity.daily",
                daily,
                daily_at_jst(settings.engine_affinity_daily_hour_jst),
                run_immediately=False,
            )
        )
    retention = timedelta(days=settings.engine_job_retention_days)
    lock_timeout = timedelta(seconds=settings.engine_job_lock_timeout_seconds)

    async def cleanup(now: datetime) -> dict[str, int]:
        reclaimed = await jobs.reclaim_stale(now=now, lock_timeout=lock_timeout)
        deleted = await jobs.cleanup(now=now, retention=retention)
        return {"reclaimed": reclaimed, "deleted": deleted}

    tasks.append(PeriodicTask("jobs.cleanup", cleanup, daily_at_jst(JOBS_CLEANUP_HOUR_JST), run_immediately=False))
    return tasks


def build_services(
    *,
    settings: Settings,
    pool: Pool,
    http: httpx.AsyncClient,
    personas: PersonaRepository,
    prompts: PromptBuilder,
    jwks_http: httpx.AsyncClient | None = None,
    llm: LLMClient | None = None,
    embedder: EmbeddingClient | None = None,
    moderator: Moderator | None = None,
    clock: Clock | None = None,
    engine_overrides: EngineOverrides | None = None,
) -> Services:
    llm = llm or create_llm_client(settings, http)
    embedder = embedder or create_embedding_client(settings, http)
    moderator = moderator or Moderator()
    clock = clock or SystemClock()
    overrides = engine_overrides or EngineOverrides()
    audit = AuditLogger(pool)
    # JWKS は LLM の接続プールとは別のクライアントで取得する（LLM の混雑で認証が待たされないように）
    jwks = JwksCache(settings.jwks_url, jwks_http or http, ttl_seconds=settings.jwks_cache_ttl_seconds)

    # ---- 安全対応（E6）と出力の追加検査（E2 / E3）
    safety = DefaultSafetyService(load_safety_config(settings.resolved_safety_resources_path))
    guard = DefaultOutputGuard()

    # ---- モジュール
    memory = overrides.memory or _build_memory(
        settings, pool=pool, llm=llm, embedder=embedder, audit=audit, personas=personas, moderator=moderator
    )
    calendar = overrides.calendar or _build_calendar(
        settings,
        pool=pool,
        llm=llm,
        embedder=embedder,
        audit=audit,
        personas=personas,
        moderator=moderator,
        guard=guard,
    )
    affinity = overrides.affinity or _build_affinity(settings, pool=pool, llm=llm, audit=audit, personas=personas)
    enabled_memory = memory if settings.engine_memory_enabled else None
    enabled_calendar = calendar if settings.engine_calendar_enabled else None
    enabled_affinity = affinity if settings.engine_affinity_enabled else None
    proactive = overrides.proactive or _build_proactive(
        settings,
        pool=pool,
        llm=llm,
        audit=audit,
        personas=personas,
        moderator=moderator,
        guard=guard,
        calendar=enabled_calendar,
        memory=enabled_memory,
        affinity=enabled_affinity,
    )
    enabled_proactive = proactive if settings.engine_proactive_enabled else None

    # ---- ジョブ・スケジューラ
    jobs = PgJobQueue(
        pool=pool,
        audit=audit,
        clock=clock,
        max_attempts=settings.engine_job_max_attempts,
        backoff_base_seconds=settings.engine_job_backoff_base_seconds,
        backoff_max_seconds=settings.engine_job_backoff_max_seconds,
    )
    registry = JobRegistry()
    handlers = EngineJobHandlers(
        settings=settings,
        pool=pool,
        queue=jobs,
        personas=personas,
        moderator=moderator,
        safety=safety,
        memory=enabled_memory,
        calendar=enabled_calendar,
        affinity=enabled_affinity,
    )
    handlers.register(registry)
    worker = Worker(
        queue=jobs,
        registry=registry,
        clock=clock,
        concurrency=settings.engine_worker_concurrency,
        poll_interval_seconds=settings.engine_worker_poll_interval_seconds,
        job_timeout_seconds=settings.engine_job_timeout_seconds,
        lock_timeout=timedelta(seconds=settings.engine_job_lock_timeout_seconds),
    )
    scope = EngineScope()
    scheduler = Scheduler(
        pool=pool,
        clock=clock,
        audit=audit,
        tasks=periodic_tasks(
            settings,
            jobs=jobs,
            calendar=enabled_calendar,
            affinity=enabled_affinity,
            proactive=enabled_proactive,
            scope=scope,
        ),
        poll_interval_seconds=settings.engine_scheduler_poll_interval_seconds,
        namespace=settings.engine_schedule_namespace,
    )

    # ---- チャット
    assembler = ContextAssembler(
        pool=pool,
        moderator=moderator,
        audit=audit,
        memory=enabled_memory,
        calendar=enabled_calendar,
        affinity=enabled_affinity,
        flags=ModuleFlags(
            memory=settings.engine_memory_enabled,
            calendar=settings.engine_calendar_enabled,
            affinity=settings.engine_affinity_enabled,
        ),
        timeout_seconds=settings.engine_context_timeout_seconds,
        history_limit=settings.short_term_message_limit,
    )
    pipeline = ChatPipeline(
        settings=settings,
        pool=pool,
        clock=clock,
        llm=llm,
        prompts=prompts,
        personas=personas,
        moderator=moderator,
        safety=safety,
        guard=guard,
        assembler=assembler,
        modules=PipelineModules(memory=enabled_memory, affinity=enabled_affinity, proactive=enabled_proactive),
        jobs=jobs,
        audit=audit,
        post_turn_enabled=handlers.post_turn_needed,
        on_job_enqueued=worker.notify,
    )
    engine = EngineServices(
        clock=clock,
        scope=scope,
        safety=safety,
        guard=guard,
        jobs=jobs,
        registry=registry,
        handlers=handlers,
        worker=worker,
        scheduler=scheduler,
        assembler=assembler,
        pipeline=pipeline,
        memory=memory,
        calendar=calendar,
        affinity=affinity,
        proactive=proactive,
    )
    return Services(
        settings=settings,
        pool=pool,
        http=http,
        jwks=jwks,
        token_verifier=TokenVerifier(settings, jwks),
        personas=personas,
        prompts=prompts,
        llm=llm,
        embedder=embedder,
        moderator=moderator,
        audit=audit,
        rate_limiter=SlidingWindowRateLimiter(
            {
                "chat": settings.rate_limit_chat_per_minute,
                "comments": settings.rate_limit_comments_per_minute,
                "memories": settings.rate_limit_memories_per_minute,
            }
        ),
        clock=clock,
        engine=engine,
        chat=ChatService(pipeline=pipeline),
        conversations=ConversationService(pool=pool, personas=personas, audit=audit, clock=clock),
        user_memories=UserMemoryService(
            settings=settings, pool=pool, embedder=embedder, moderator=moderator, audit=audit, clock=clock
        ),
        comments=CommentService(
            settings=settings,
            pool=pool,
            llm=llm,
            personas=personas,
            moderator=moderator,
            audit=audit,
            prompts=prompts,
        ),
    )


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


class RateLimit:
    """ユーザー単位のレート制限を行う依存関係（認証も兼ねる）。"""

    def __init__(self, bucket: str) -> None:
        self.bucket = bucket

    async def __call__(self, user: CurrentUserDep, services: ServicesDep) -> CurrentUser:
        decision = services.rate_limiter.check(self.bucket, str(user.id))
        if not decision.allowed:
            logger.warning(
                "rate limited",
                extra={
                    "fields": {
                        "bucket": self.bucket,
                        "user_id": str(user.id),
                        "retry_after": decision.retry_after_seconds,
                    }
                },
            )
            raise ApiError(429, "rate_limited", headers={"Retry-After": str(decision.retry_after_seconds)})
        return user


ChatRateLimitedUser = Annotated[CurrentUser, Depends(RateLimit("chat"))]
CommentRateLimitedUser = Annotated[CurrentUser, Depends(RateLimit("comments"))]
MemoryWriteRateLimitedUser = Annotated[CurrentUser, Depends(RateLimit("memories"))]
