"""POST /chat の処理本体（仕様 §7 / BRIEF §2.5）。ルーターは薄く保ち、ここで全体を組み立てる。

1. （ルーター）JWT 検証・退会チェック・レート制限
2. 会話の所有者・キャラ一致・キャラ有効を確認（不一致は 404）
3. audit chat.request
4. Gate #1（入力）。ヒット時は定型文で応答し LLM / 記憶抽出をスキップ
5. 短期メモリ（直近 N ターン）。検索用の埋め込みと並行して取得
6. 長期メモリ検索（厳密コサイン検索 → 重要度で再ランク + 最新要約）。記憶抽出はこの時点で開始する
7. プロンプト組立（ペルソナ YAML + テンプレート + 記憶 + 履歴）
8. 応答生成と記憶抽出を並行実行
9. Gate #1（出力）。ヒット時は定型文に差し替え
10. ユーザー発言・キャラ返答を1トランザクションで保存
11. 記憶の保存（重要度 ≥ 閾値、近似重複は更新、ユーザー編集済みは上書きしない）
12. 中期要約（BackgroundTask）
13. audit chat.response
LLM 障害時は audit llm.error を記録し 503。メッセージは保存しない。

全体の上限は `CHAT_DEADLINE_SECONDS`（Web の CHAT_TIMEOUT_MS より短い）。5〜8 の応答生成がこれを超えたら
503（何も保存しない）。記憶抽出の待ちと記憶の保存は残り時間で打ち切り、チャット自体は成功させる。
クライアントが諦めた後に遅れて保存され、再送で二重になることを防ぐ。
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
from app.services.embedding import EmbeddingError, embedding_failure_payload
from app.services.llm import LLMClient, LLMError, LLMRequest, MockHints, clean_reply
from app.services.memory import ExtractionResult, MemoryEngine, SavedMemories
from app.services.moderation import ModerationResult, Moderator
from app.services.persona import Persona, PersonaRepository
from app.services.prompt import PromptBuilder
from app.services.types import HistoryItem

logger = get_logger("chat")

REPLY_MAX_CHARS: Final[int] = 2000
# 返答生成後、記憶の保存に最低限与える秒数（締め切り間際でも数秒は待つ。Web 側の上限には余裕がある）
SAVE_MEMORIES_MIN_SECONDS: Final[float] = 3.0

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
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._settings.chat_deadline_seconds
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

        # 5〜8. 締め切り（CHAT_DEADLINE_SECONDS）付きで、メモリ取得・プロンプト組立・応答生成を行う
        extraction: asyncio.Task[ExtractionResult] | None = None
        budget = asyncio.timeout_at(deadline)
        try:
            async with budget:
                # 5. 短期メモリの取得と、長期メモリ検索用の埋め込みは互いに独立なので並行して行う
                #    （埋め込みは EMBEDDING_TIMEOUT_SECONDS で打ち切り、失敗時は検索を省略する）
                history, query_embedding = await self._load_history_and_embedding(
                    conversation_id, request.message, user_id=user.id, character_id=character.id
                )
                # 8'. 記憶抽出は短期メモリだけで行えるので、長期メモリの検索・応答生成と並行して先に始める
                extraction = asyncio.create_task(
                    self._memory.extract_candidates(
                        persona,
                        history=history,
                        user_message=request.message,
                        now=now,
                        user_id=user.id,
                        character_id=character.id,
                        conversation_id=conversation_id,
                    )
                )
                # 6. 長期メモリ
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

                # 8. 応答生成（記憶抽出は上で開始済み・並行実行）
                result = await self._llm.complete(llm_request)
                reply = clean_reply(result.text, persona.name, max_chars=REPLY_MAX_CHARS)
                if not reply:
                    raise LLMError("empty reply after cleanup")
        except (LLMError, TimeoutError) as exc:
            if isinstance(exc, TimeoutError) and not budget.expired():
                raise  # 締め切り以外の TimeoutError（DB 接続待ちなど）は 500 として扱う
            if extraction is not None:
                await _cancel(extraction)
            if isinstance(exc, LLMError):
                error, status_code, attempts = str(exc), exc.status_code, exc.attempts
            else:
                error = f"deadline exceeded ({self._settings.chat_deadline_seconds:g}s)"
                status_code, attempts = None, None
            await self._audit.log(
                "llm.error",
                user_id=user.id,
                character_id=character.id,
                payload={
                    "purpose": "chat",
                    "conversation_id": conversation_id,
                    "error": error,
                    "status_code": status_code,
                    "attempts": attempts,
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                },
            )
            raise ApiError(503, "llm_unavailable") from exc
        except BaseException:
            if extraction is not None:
                await _cancel(extraction)
            raise
        extracted = await self._await_extraction(
            extraction,
            remaining_seconds=deadline - loop.time(),
            user_id=user.id,
            character_id=character.id,
            conversation_id=conversation_id,
        )

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

        # 11. 記憶の保存（失敗・時間切れでもチャット自体は成功扱い。ただし監査ログには残す）
        saved = SavedMemories()
        memory_save_error: str | None = None
        save_timeout = max(deadline - loop.time(), SAVE_MEMORIES_MIN_SECONDS)
        try:
            async with asyncio.timeout(save_timeout):
                saved = await self._memory.save_candidates(
                    user_id=user.id,
                    character_id=character.id,
                    source_message_id=user_message.id,
                    candidates=extracted.candidates,
                )
        except (EmbeddingError, asyncpg.PostgresError, OSError, TimeoutError) as exc:
            failure = embedding_failure_payload(exc, timeout_seconds=save_timeout)
            memory_save_error = failure["error"]
            logger.error(
                "failed to save extracted memories",
                extra={"fields": {"conversation_id": str(conversation_id), "error": repr(exc)}},
            )
            if isinstance(exc, EmbeddingError | TimeoutError):
                # 埋め込み障害・時間切れは llm.error にも残す（抽出した記憶が失われたことを障害として検知できるように）
                await self._memory.log_embedding_error(
                    "memory_save",
                    failure,
                    user_id=user.id,
                    character_id=character.id,
                    conversation_id=conversation_id,
                    user_message_id=user_message.id,
                    lost_candidates=len(extracted.candidates),
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
            "memories_created": saved.created,
            "memories_updated": saved.updated,
            "memories_skipped_user_edited": saved.skipped_user_edited,
            "memory_candidates": len(extracted.candidates),
            "extraction": extracted.audit_payload(
                threshold=self._settings.memory_importance_threshold,
                include_prompt=self._settings.audit_log_prompts,
            ),
            "history_messages": len(history),
            # 検索用の埋め込みに失敗して長期記憶を使わなかった / 抽出した記憶を保存できなかった（llm.error も残る）
            "retrieval_skipped": query_embedding is None,
            "memory_save_error": memory_save_error,
            # LLM に渡したプロンプトの文字数（履歴は prompt.HISTORY_MAX_CHARS で打ち切る。待ち時間・費用の目安）
            "prompt_chars": sum(len(m["content"]) for m in prompt_messages),
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
            memories_created=saved.created,
            user_message=user_message,
            character_message=character_message,
            moderated=moderated,
        )

    async def _load_history_and_embedding(
        self, conversation_id: UUID, message: str, *, user_id: UUID, character_id: UUID
    ) -> tuple[list[HistoryItem], list[float] | None]:
        embedding = asyncio.create_task(
            self._memory.embed_query(
                message, user_id=user_id, character_id=character_id, conversation_id=conversation_id
            )
        )
        try:
            async with self._pool.acquire() as conn:
                history = await self._memory.fetch_short_term(conn, conversation_id)
            return history, await embedding
        finally:
            # 履歴の取得に失敗した・締め切りで取り消された場合に、埋め込みだけが残って走り続けないようにする
            await _cancel(embedding)

    async def _await_extraction(
        self,
        task: asyncio.Task[ExtractionResult],
        *,
        remaining_seconds: float,
        user_id: UUID,
        character_id: UUID,
        conversation_id: UUID,
    ) -> ExtractionResult:
        """返答ができた後、記憶抽出は締め切りまでだけ待つ（間に合わなければ候補なし）。"""
        try:
            return await asyncio.wait_for(task, timeout=max(remaining_seconds, 0.0))
        except TimeoutError:
            error = f"deadline exceeded ({self._settings.chat_deadline_seconds:g}s)"
            logger.error(
                "memory extraction timed out; continuing without candidates",
                extra={"fields": {"conversation_id": str(conversation_id)}},
            )
            await self._memory.log_extraction_error(
                error, user_id=user_id, character_id=character_id, conversation_id=conversation_id
            )
            return ExtractionResult(candidates=[], error=error)

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
