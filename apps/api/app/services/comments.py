"""コメント投稿と、投稿者キャラによる返信生成。

- POST /comments: Gate #1（入力）でヒットしたら 422 moderation_blocked（保存しない）。
  保存後、確率 `COMMENT_AUTO_REPLY_PROBABILITY` で投稿者キャラの返信を BackgroundTask として予約する。
  バックグラウンド処理はリクエストのスコープ外で動くため、自前でプールから接続を取得する。
- POST /comments/generate: 投稿者キャラが指定コメントに今すぐ返信する。
  出力が Gate #1 でヒットした場合は保存せず `comment: null`。
"""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final, Literal
from uuid import UUID

import asyncpg
from fastapi import BackgroundTasks

from app.core.config import Settings
from app.core.db import Pool
from app.core.errors import ApiError, not_found
from app.core.logging import get_logger
from app.core.security import CurrentUser
from app.models.comments import (
    CommentDTO,
    CreateCommentRequest,
    CreateCommentResponse,
    GenerateCommentRequest,
    GenerateCommentResponse,
)
from app.services.audit import AuditLogger
from app.services.characters import CHARACTER_COLUMNS, COMMENT_COLUMNS, character_from_row, comment_dto
from app.services.llm import LLMClient, LLMError, LLMRequest, MockHints, clean_reply
from app.services.moderation import Moderator
from app.services.persona import PersonaRepository
from app.services.prompt import PromptBuilder

logger = get_logger("comments")

COMMENT_REPLY_MAX_TOKENS: Final[int] = 120
COMMENT_REPLY_MAX_CHARS: Final[int] = 200
_COMMENT_BLOCKED_MESSAGE: Final[str] = "このコメントは投稿できません。表現を変えて再度お試しください。"

# 公開済み（published_at <= now()）かつ有効キャラの投稿のみ対象（クライアントの RLS と同じ条件）
_VISIBLE_POST_SQL: Final[str] = """
select p.id, p.character_id, p.caption
  from public.posts p
  join public.characters ch on ch.id = p.character_id
 where p.id = $1 and p.published_at <= now() and ch.is_active
"""

_INSERT_COMMENT_SQL: Final[str] = f"""
insert into public.comments (post_id, parent_comment_id, author_type, author_user_id, author_character_id, body)
values ($1, $2, $3, $4, $5, $6)
returning {COMMENT_COLUMNS}
"""  # noqa: S608 - 列名は定数

TriggerKind = Literal["auto", "manual"]


class CommentService:
    def __init__(
        self,
        *,
        settings: Settings,
        pool: Pool,
        llm: LLMClient,
        personas: PersonaRepository,
        moderator: Moderator,
        audit: AuditLogger,
        prompts: PromptBuilder,
        random_source: Callable[[], float] = random.random,
    ) -> None:
        self._settings = settings
        self._pool = pool
        self._llm = llm
        self._personas = personas
        self._moderator = moderator
        self._audit = audit
        self._prompts = prompts
        self._random = random_source

    # ------------------------------------------------------------------ POST /comments
    async def create(
        self, user: CurrentUser, request: CreateCommentRequest, background: BackgroundTasks
    ) -> CreateCommentResponse:
        async with self._pool.acquire() as conn:
            post = await conn.fetchrow(_VISIBLE_POST_SQL, request.post_id)
            if post is None:
                raise not_found("投稿が見つかりません。")
            if request.parent_comment_id is not None:
                parent_ok = await conn.fetchval(
                    "select 1 from public.comments where id = $1 and post_id = $2",
                    request.parent_comment_id,
                    request.post_id,
                )
                if parent_ok is None:
                    raise not_found("返信先のコメントが見つかりません。")

        result = self._moderator.check(request.body)
        if result.flagged:
            await self._audit.log(
                "moderation.flag",
                user_id=user.id,
                character_id=post["character_id"],
                payload={
                    "stage": "input",
                    "context": "comment",
                    "post_id": request.post_id,
                    "categories": result.categories,
                    "matched_terms": result.matched_terms,
                    "text": request.body,
                },
            )
            raise ApiError(422, "moderation_blocked", _COMMENT_BLOCKED_MESSAGE)

        row = await self._pool.fetchrow(
            _INSERT_COMMENT_SQL,
            request.post_id,
            request.parent_comment_id,
            "user",
            user.id,
            None,
            request.body,
        )
        if row is None:  # pragma: no cover
            raise RuntimeError("comment insert returned no row")
        comment = comment_dto(row)
        await self._audit.log(
            "comment.create",
            user_id=user.id,
            character_id=post["character_id"],
            payload={
                "comment_id": comment.id,
                "post_id": comment.post_id,
                "parent_comment_id": comment.parent_comment_id,
                "body": comment.body,
            },
        )

        reply_scheduled = self._random() < self._settings.comment_auto_reply_probability
        if reply_scheduled:
            background.add_task(
                self.reply_in_background, post_id=comment.post_id, comment_id=comment.id, user_id=user.id
            )
        return CreateCommentResponse(comment=comment, reply_scheduled=reply_scheduled)

    # ------------------------------------------------------------------ POST /comments/generate
    async def generate(self, user: CurrentUser, request: GenerateCommentRequest) -> GenerateCommentResponse:
        async with self._pool.acquire() as conn:
            post = await conn.fetchrow(_VISIBLE_POST_SQL, request.post_id)
            if post is None:
                raise not_found("投稿が見つかりません。")
            parent = await conn.fetchrow(
                f"select {COMMENT_COLUMNS} from public.comments where id = $1 and post_id = $2",  # noqa: S608
                request.parent_comment_id,
                request.post_id,
            )
        if parent is None:
            raise not_found("返信先のコメントが見つかりません。")
        if parent["author_type"] == "character" and parent["author_character_id"] == post["character_id"]:
            raise ApiError(422, "validation_error", "キャラクター自身のコメントには返信できません。")
        try:
            comment = await self._generate_reply(post, parent, requested_by=user.id, trigger="manual")
        except LLMError as exc:
            await self._log_llm_error(exc, user.id, post["character_id"], request.post_id, request.parent_comment_id)
            raise ApiError(503, "llm_unavailable") from exc
        return GenerateCommentResponse(comment=comment)

    # ------------------------------------------------------------------ background
    async def reply_in_background(self, *, post_id: UUID, comment_id: UUID, user_id: UUID) -> None:
        """BackgroundTask 用。例外は外に出さず、ログと監査ログに残す。"""
        character_id: UUID | None = None
        try:
            async with self._pool.acquire() as conn:
                post = await conn.fetchrow(_VISIBLE_POST_SQL, post_id)
                parent = await conn.fetchrow(
                    f"select {COMMENT_COLUMNS} from public.comments where id = $1 and post_id = $2",  # noqa: S608
                    comment_id,
                    post_id,
                )
            if post is None or parent is None:
                logger.warning(
                    "auto reply skipped: post or comment disappeared",
                    extra={"fields": {"post_id": str(post_id), "comment_id": str(comment_id)}},
                )
                return
            character_id = post["character_id"]
            await self._generate_reply(post, parent, requested_by=user_id, trigger="auto")
        except LLMError as exc:
            await self._log_llm_error(exc, user_id, character_id, post_id, comment_id)
        except Exception:
            logger.exception(
                "auto reply failed",
                extra={"fields": {"post_id": str(post_id), "comment_id": str(comment_id)}},
            )

    # ------------------------------------------------------------------ 共通
    async def _generate_reply(
        self,
        post: asyncpg.Record,
        parent: asyncpg.Record,
        *,
        requested_by: UUID,
        trigger: TriggerKind,
    ) -> CommentDTO | None:
        character_row = await self._pool.fetchrow(
            f"select {CHARACTER_COLUMNS} from public.characters where id = $1",  # noqa: S608 - 列名は定数
            post["character_id"],
        )
        if character_row is None:
            return None
        character = character_from_row(character_row)
        persona = self._personas.for_character(character)
        now = datetime.now(UTC)
        messages = self._prompts.comment_reply_messages(
            persona, post_caption=post["caption"], comment_body=parent["body"]
        )
        result = await self._llm.complete(
            LLMRequest(
                purpose="comment_reply",
                messages=messages,
                temperature=self._settings.llm_temperature,
                max_tokens=COMMENT_REPLY_MAX_TOKENS,
                hints=MockHints(persona=persona, now=now, post_caption=post["caption"], comment_body=parent["body"]),
            )
        )
        first_line = result.text.strip().splitlines()[0] if result.text.strip() else ""
        text = clean_reply(first_line, persona.name, max_chars=COMMENT_REPLY_MAX_CHARS)
        if not text:
            raise LLMError("empty comment reply")

        check = self._moderator.check(text, extra_ng_words=persona.speech.ng_words)
        if check.flagged:
            await self._audit.log(
                "moderation.flag",
                user_id=requested_by,
                character_id=character.id,
                payload={
                    "stage": "output",
                    "context": "comment_reply",
                    "post_id": post["id"],
                    "parent_comment_id": parent["id"],
                    "categories": check.categories,
                    "matched_terms": check.matched_terms,
                    "text": text,
                },
            )
            return None

        row = await self._pool.fetchrow(
            _INSERT_COMMENT_SQL, post["id"], parent["id"], "character", None, character.id, text
        )
        if row is None:  # pragma: no cover
            raise RuntimeError("comment insert returned no row")
        comment = comment_dto(row)
        payload: dict[str, Any] = {
            "comment_id": comment.id,
            "post_id": comment.post_id,
            "parent_comment_id": comment.parent_comment_id,
            "body": comment.body,
            "trigger": trigger,
            "model": result.model,
            "latency_ms": result.latency_ms,
            "usage": result.usage,
        }
        if self._settings.audit_log_prompts:
            payload["prompt_messages"] = messages
        await self._audit.log("comment.generate", user_id=requested_by, character_id=character.id, payload=payload)
        return comment

    async def _log_llm_error(
        self,
        exc: LLMError,
        user_id: UUID,
        character_id: UUID | None,
        post_id: UUID,
        parent_comment_id: UUID,
    ) -> None:
        await self._audit.log(
            "llm.error",
            user_id=user_id,
            character_id=character_id,
            payload={
                "purpose": "comment_reply",
                "post_id": post_id,
                "parent_comment_id": parent_comment_id,
                "error": str(exc),
                "status_code": exc.status_code,
                "attempts": exc.attempts,
            },
        )
