"""POST /chat の処理本体（仕様 §7 / BRIEF §2.5）。ルーターは薄く保ち、ここで全体を組み立てる。

1. （ルーター）JWT 検証・退会チェック・レート制限
2. 会話の所有者・キャラ一致・キャラ有効を確認（不一致は 404）
3. audit chat.request
4. Gate #1（入力）。ヒット時は定型文で応答し LLM / 記憶抽出をスキップ
5. 短期メモリ（直近 N ターン）
6. 長期メモリ検索（厳密コサイン検索 → 重要度で再ランク + 最新要約）
7. プロンプト組立（ペルソナ YAML + テンプレート + 記憶 + 履歴）
8. 応答生成と記憶抽出を並行実行
9. Gate #1（出力）。ヒット時は定型文に差し替え
10. ユーザー発言・キャラ返答を1トランザクションで保存
11. 記憶の保存（重要度 ≥ 閾値、近似重複は更新、ユーザー編集済みは上書きしない）
12. 中期要約（BackgroundTask）
13. audit chat.response
LLM 障害時は audit llm.error を記録し 503。メッセージは保存しない。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
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
from app.models.dm import ChatRequest, ChatResponse, MessageDTO
from app.services.audit import AuditLogger
from app.services.characters import MESSAGE_COLUMNS, character_from_row, message_dto
from app.services.embedding import EmbeddingError
from app.services.llm import LLMClient, LLMError, LLMRequest, MockHints, clean_reply
from app.services.memory import MemoryEngine
from app.services.moderation import ModerationResult, Moderator
from app.services.persona import Persona, PersonaRepository
from app.services.prompt import PromptBuilder
from app.services.types import MemoryCandidate

logger = get_logger("chat")

REPLY_MAX_CHARS: Final[int] = 2000

_CONVERSATION_SQL: Final[str] = """
select ch.id, ch.handle, ch.name, ch.avatar_url, ch.bio, ch.persona_key, ch.system_prompt, ch.is_active
  from public.conversations c
  join public.characters ch on ch.id = c.character_id
 where c.id = $1 and c.user_id = $2 and c.character_id = $3 and ch.is_active
"""

_INSERT_MESSAGE_SQL: Final[str] = f"""
insert into public.messages (conversation_id, sender_type, body)
values ($1, $2, $3)
returning {MESSAGE_COLUMNS}
"""  # noqa: S608 - 列名は定数


class ChatService:
    def __init__(
        self,
        *,
        settings: Settings,
        pool: Pool,
        llm: LLMClient,
        memory: MemoryEngine,
        prompts: PromptBuilder,
        personas: PersonaRepository,
        moderator: Moderator,
        audit: AuditLogger,
    ) -> None:
        self._settings = settings
        self._pool = pool
        self._llm = llm
        self._memory = memory
        self._prompts = prompts
        self._personas = personas
        self._moderator = moderator
        self._audit = audit

    async def chat(self, user: CurrentUser, request: ChatRequest, background: BackgroundTasks) -> ChatResponse:
        started = time.perf_counter()
        now = datetime.now(UTC)
        conversation_id = request.conversation_id

        # 2. 所有者チェック（他人の会話・キャラ不一致・無効キャラはすべて 404）
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_CONVERSATION_SQL, conversation_id, user.id, request.character_id)
        if row is None:
            raise not_found("会話が見つかりません。")
        character = character_from_row(row)
        persona = self._personas.for_character(character)

        # 3. audit chat.request
        await self._audit.log(
            "chat.request",
            user_id=user.id,
            character_id=character.id,
            payload={"conversation_id": conversation_id, "message": request.message},
        )

        # 4. Gate #1（入力）
        input_check = self._moderator.check(request.message)
        if input_check.flagged:
            await self._flag(
                "input",
                result=input_check,
                text=request.message,
                user_id=user.id,
                character_id=character.id,
                conversation_id=conversation_id,
            )
            return await self._respond_moderated(
                user_id=user.id,
                character_id=character.id,
                conversation_id=conversation_id,
                persona=persona,
                message=request.message,
                started=started,
            )

        # 5〜6. 短期・長期メモリ
        async with self._pool.acquire() as conn:
            history = await self._memory.fetch_short_term(conn, conversation_id)
        query_embedding = await self._memory.embed_query(request.message)
        async with self._pool.acquire() as conn:
            memories = await self._memory.retrieve(
                conn, user_id=user.id, character_id=character.id, query_embedding=query_embedding
            )

        # 7. プロンプト
        prompt_messages = self._prompts.chat_messages(
            persona, memories=memories, history=history, user_message=request.message, now=now
        )
        llm_request = LLMRequest(
            purpose="chat",
            messages=prompt_messages,
            temperature=self._settings.llm_temperature,
            max_tokens=self._settings.llm_max_tokens,
            hints=MockHints(
                persona=persona,
                now=now,
                user_message=request.message,
                memories=tuple(memories),
                history=tuple(history),
            ),
        )

        # 8. 応答生成と記憶抽出を並行実行
        extraction = asyncio.create_task(
            self._memory.extract_candidates(persona, history=history, user_message=request.message, now=now)
        )
        try:
            result = await self._llm.complete(llm_request)
            reply = clean_reply(result.text, persona.name, max_chars=REPLY_MAX_CHARS)
            if not reply:
                raise LLMError("empty reply after cleanup")
        except LLMError as exc:
            await _cancel(extraction)
            await self._audit.log(
                "llm.error",
                user_id=user.id,
                character_id=character.id,
                payload={
                    "purpose": "chat",
                    "conversation_id": conversation_id,
                    "error": str(exc),
                    "status_code": exc.status_code,
                    "attempts": exc.attempts,
                },
            )
            raise ApiError(503, "llm_unavailable") from exc
        except BaseException:
            await _cancel(extraction)
            raise
        candidates: list[MemoryCandidate] = await extraction

        # 9. Gate #1（出力）
        moderated = False
        output_check = self._moderator.check(reply, extra_ng_words=persona.speech.ng_words)
        if output_check.flagged:
            await self._flag(
                "output",
                result=output_check,
                text=reply,
                user_id=user.id,
                character_id=character.id,
                conversation_id=conversation_id,
            )
            reply = persona.refusal_reply
            moderated = True

        # 10. 保存（1トランザクション）
        user_message, character_message = await self._save_messages(conversation_id, request.message, reply)

        # 11. 記憶の保存（失敗してもチャット自体は成功扱い。ただしログは残す）
        memories_created: list[UUID] = []
        try:
            memories_created = await self._memory.save_candidates(
                user_id=user.id,
                character_id=character.id,
                source_message_id=user_message.id,
                candidates=candidates,
            )
        except (EmbeddingError, asyncpg.PostgresError, OSError) as exc:
            logger.error(
                "failed to save extracted memories",
                extra={"fields": {"conversation_id": str(conversation_id), "error": repr(exc)}},
            )

        # 12. 中期要約（レスポンス送信後）
        background.add_task(
            self._memory.maybe_summarize,
            conversation_id=conversation_id,
            user_id=user.id,
            character_id=character.id,
            persona=persona,
            now=now,
        )

        # 13. audit chat.response
        memories_used = [m.id for m in memories]
        payload: dict[str, Any] = {
            "conversation_id": conversation_id,
            "user_message_id": user_message.id,
            "message_id": character_message.id,
            "reply": reply,
            "moderated": moderated,
            "moderation_stage": "output" if moderated else None,
            "model": result.model,
            "llm_latency_ms": result.latency_ms,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "usage": result.usage,
            "memories_used": memories_used,
            "memories_created": memories_created,
            "memory_candidates": len(candidates),
            "history_messages": len(history),
            "persona_key": persona.key,
            "persona_fallback": persona.is_fallback,
        }
        if self._settings.audit_log_prompts:
            payload["prompt_messages"] = prompt_messages
        await self._audit.log("chat.response", user_id=user.id, character_id=character.id, payload=payload)

        return ChatResponse(
            message_id=character_message.id,
            reply=reply,
            memories_used=memories_used,
            memories_created=memories_created,
            user_message=user_message,
            character_message=character_message,
            moderated=moderated,
        )

    async def _respond_moderated(
        self,
        *,
        user_id: UUID,
        character_id: UUID,
        conversation_id: UUID,
        persona: Persona,
        message: str,
        started: float,
    ) -> ChatResponse:
        reply = persona.refusal_reply
        user_message, character_message = await self._save_messages(conversation_id, message, reply)
        await self._audit.log(
            "chat.response",
            user_id=user_id,
            character_id=character_id,
            payload={
                "conversation_id": conversation_id,
                "user_message_id": user_message.id,
                "message_id": character_message.id,
                "reply": reply,
                "moderated": True,
                "moderation_stage": "input",
                "model": None,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "memories_used": [],
                "memories_created": [],
                "persona_key": persona.key,
            },
        )
        return ChatResponse(
            message_id=character_message.id,
            reply=reply,
            memories_used=[],
            memories_created=[],
            user_message=user_message,
            character_message=character_message,
            moderated=True,
        )

    async def _save_messages(
        self, conversation_id: UUID, user_body: str, character_body: str
    ) -> tuple[MessageDTO, MessageDTO]:
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                user_row = await conn.fetchrow(_INSERT_MESSAGE_SQL, conversation_id, "user", user_body)
                character_row = await conn.fetchrow(_INSERT_MESSAGE_SQL, conversation_id, "character", character_body)
        except asyncpg.ForeignKeyViolationError as exc:
            # 検証後に会話が削除された
            raise not_found("会話が見つかりません。") from exc
        if user_row is None or character_row is None:  # pragma: no cover - returning 付き insert
            raise RuntimeError("message insert returned no row")
        return message_dto(user_row), message_dto(character_row)

    async def _flag(
        self,
        stage: Literal["input", "output"],
        *,
        result: ModerationResult,
        text: str,
        user_id: UUID,
        character_id: UUID,
        conversation_id: UUID,
    ) -> None:
        await self._audit.log(
            "moderation.flag",
            user_id=user_id,
            character_id=character_id,
            payload={
                "stage": stage,
                "context": "chat",
                "conversation_id": conversation_id,
                "categories": result.categories,
                "matched_terms": result.matched_terms,
                "text": text,
            },
        )


async def _cancel(task: asyncio.Task[Any]) -> None:
    if task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
