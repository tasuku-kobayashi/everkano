"""アプリ全体で共有するサービス群（lifespan で生成し `app.state.services` に保持）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import Depends, Request

from app.core.config import Settings
from app.core.db import Pool
from app.core.errors import ApiError
from app.core.logging import get_logger
from app.core.security import CurrentUser, CurrentUserDep, JwksCache, TokenVerifier
from app.services.audit import AuditLogger
from app.services.chat import ChatService
from app.services.comments import CommentService
from app.services.conversations import ConversationService
from app.services.embedding import EmbeddingClient, create_embedding_client
from app.services.llm import LLMClient, create_llm_client
from app.services.memory import MemoryEngine
from app.services.moderation import Moderator
from app.services.persona import PersonaRepository
from app.services.prompt import PromptBuilder
from app.services.rate_limit import SlidingWindowRateLimiter
from app.services.user_memories import UserMemoryService

logger = get_logger("container")


@dataclass(frozen=True, slots=True)
class Services:
    settings: Settings
    pool: Pool
    http: httpx.AsyncClient
    token_verifier: TokenVerifier
    personas: PersonaRepository
    prompts: PromptBuilder
    llm: LLMClient
    embedder: EmbeddingClient
    moderator: Moderator
    audit: AuditLogger
    rate_limiter: SlidingWindowRateLimiter
    memory: MemoryEngine
    chat: ChatService
    conversations: ConversationService
    user_memories: UserMemoryService
    comments: CommentService


def build_services(
    *,
    settings: Settings,
    pool: Pool,
    http: httpx.AsyncClient,
    personas: PersonaRepository,
    prompts: PromptBuilder,
    llm: LLMClient | None = None,
    embedder: EmbeddingClient | None = None,
    moderator: Moderator | None = None,
) -> Services:
    llm = llm or create_llm_client(settings, http)
    embedder = embedder or create_embedding_client(settings, http)
    moderator = moderator or Moderator()
    audit = AuditLogger(pool)
    jwks = JwksCache(settings.jwks_url, http, ttl_seconds=settings.jwks_cache_ttl_seconds)
    memory = MemoryEngine(
        settings=settings, pool=pool, embedder=embedder, llm=llm, prompts=prompts, audit=audit, moderator=moderator
    )
    return Services(
        settings=settings,
        pool=pool,
        http=http,
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
            }
        ),
        memory=memory,
        chat=ChatService(
            settings=settings,
            pool=pool,
            llm=llm,
            memory=memory,
            prompts=prompts,
            personas=personas,
            moderator=moderator,
            audit=audit,
        ),
        conversations=ConversationService(pool=pool, personas=personas, audit=audit),
        user_memories=UserMemoryService(pool=pool, embedder=embedder, moderator=moderator, audit=audit),
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
