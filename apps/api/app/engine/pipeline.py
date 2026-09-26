"""DM の返答パイプライン（`POST /chat/stream` と `POST /chat` で共通。ENGINE_BRIEF §2.4 / E8）。

順序:
  1.（ルーター）認証・退会チェック・レート制限 → `prepare()`: 会話の所有者・キャラの一致・有効を確認（404）
  2. audit chat.request
  3. **E6 の安全対応を最初に判定**: 検知したら LLM を使わず、キャラの声の気づかいの言葉 + 相談窓口を返す
     （`replace` reason=safety。好感度の評価から外す。audit safety.trigger。保存する返答に messages.safety_triggered
     の印を付け、どの端末・履歴でも相談窓口のカードを出せるようにする）
  4. Gate #1（入力）: 引っかかったら定型の断り文（`replace` reason=moderated）
  5. Context Assembler（履歴・記憶・カレンダー・好感度を並行に取得。締め切りを超えた要素は省く）
  6. LLM のストリーミング → 文単位のフラッシュ（送る前に Gate #1 + キャラの NG ワード + OutputGuard で検査）
     → 最終判定で引っかかれば `replace`（キャラの断り文）
  7. ユーザー発言とキャラ返答を1トランザクションで保存（時刻は時計から: ユーザー = now、キャラ = +1ms。
     同じ会話の直前のメッセージより必ず後になるようにする）
  8. proactive.on_user_message（未返信の自発メッセージに replied_at）+ affinity.touch_interaction
  9. `post_turn` ジョブを登録（記憶の抽出・約束の予定化・好感度の評価は**返答の後に非同期**で行う。E8）
  10. audit chat.response（ttft_ms・予算の内訳・使った記憶・状態・段階・usage）→ `done`
  11.（返答の後）使った記憶の mark_referenced
LLM の障害・締め切り（CHAT_DEADLINE_SECONDS）超過は `error`（503 llm_unavailable。何も保存しない）。

パイプラインはイベントを `emit` に渡すだけ（SSE への変換はルーター、/chat は最後の done を返す）。
呼び出し側（ChatService）はパイプラインを別タスクで動かすので、クライアントが接続を切っても最後まで
生成・保存される（再送で二重にならないよう、保存したものは次の画面表示で届く）。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Literal
from uuid import UUID

import asyncpg

from app.core.config import Settings
from app.core.db import Pool
from app.core.errors import ApiError, ApiErrorCode, not_found
from app.core.logging import get_logger
from app.core.security import CurrentUser
from app.engine.context_assembler import AssembledContext, ContextAssembler
from app.engine.jobs.handlers import JOB_MEMORY_SUMMARIZE, JOB_POST_TURN
from app.engine.jobs.queue import PgJobQueue
from app.engine.pipeline_flush import OutputChecker, OutputFlag, StreamFlusher
from app.engine.safety.service import DefaultSafetyService
from app.engine.types import AffinityService, Clock, MemoryService, OutputGuard, ProactiveService, SafetyAssessment
from app.models.dm import ChatRequest, ChatResponse, MessageDTO, SafetyInfo, SafetyResource
from app.services.audit import AuditLogger
from app.services.characters import MESSAGE_COLUMNS, character_from_row, message_dto
from app.services.llm import LLMClient, LLMError, LLMRequest, LLMStream, MockHints, stream_completion
from app.services.moderation import Moderator
from app.services.persona import Persona, PersonaRepository
from app.services.prompt import ChatMessage, PromptBuilder
from app.services.types import CharacterRecord

logger = get_logger("engine.pipeline")

_CONVERSATION_SQL: Final[str] = """
select ch.id, ch.handle, ch.name, ch.avatar_url, ch.bio, ch.persona_key, ch.system_prompt, ch.is_active
  from public.conversations c
  join public.characters ch on ch.id = c.character_id
 where c.id = $1 and c.user_id = $2 and c.character_id = $3 and ch.is_active
"""

# 時計の時刻で保存する。同じ会話の直前のメッセージより必ず後にする（時計を止めた評価ハーネスでも順序が崩れない）
_INSERT_USER_MESSAGE_SQL: Final[str] = f"""
insert into public.messages (conversation_id, sender_type, body, created_at)
values (
  $1, 'user', $2,
  greatest(
    $3::timestamptz,
    coalesce((select max(created_at) from public.messages where conversation_id = $1), '-infinity'::timestamptz)
      + interval '1 millisecond'
  )
)
returning {MESSAGE_COLUMNS}
"""  # noqa: S608 - 列名は定数

# safety_triggered（E6）: 安全対応の返答に印を残す（どの端末・履歴でも相談窓口のカードを出すため）
_INSERT_CHARACTER_MESSAGE_SQL: Final[str] = f"""
insert into public.messages (conversation_id, sender_type, body, created_at, safety_triggered)
values ($1, 'character', $2, $3, $4)
returning {MESSAGE_COLUMNS}
"""  # noqa: S608 - 列名は定数

REPLY_GAP: Final[timedelta] = timedelta(milliseconds=1)


# ---------------------------------------------------------------------------
# イベント（SSE の ChatStreamEvent と対応。packages/shared/src/api.ts）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeltaEvent:
    text: str
    type: Literal["delta"] = "delta"


@dataclass(frozen=True, slots=True)
class ReplaceEvent:
    text: str
    reason: Literal["moderated", "safety"]
    type: Literal["replace"] = "replace"


@dataclass(frozen=True, slots=True)
class DoneEvent:
    response: ChatResponse
    type: Literal["done"] = "done"


@dataclass(frozen=True, slots=True)
class ErrorEvent:
    status_code: int
    code: ApiErrorCode
    message: str | None = None
    type: Literal["error"] = "error"

    def to_api_error(self) -> ApiError:
        return ApiError(self.status_code, self.code, self.message)


PipelineEvent = DeltaEvent | ReplaceEvent | DoneEvent | ErrorEvent
Emit = Callable[[PipelineEvent], None]


@dataclass(frozen=True, slots=True)
class ChatTurn:
    """所有者の確認を終えた1回の発言（prepare の結果）。"""

    user_id: UUID
    character: CharacterRecord
    persona: Persona
    conversation_id: UUID
    message: str
    now: datetime
    started: float  # time.perf_counter()（ttft の起点）
    streamed: bool


@dataclass(frozen=True, slots=True)
class PipelineModules:
    """パイプラインが使うエンジンのモジュール（無効・未接続なら None）。"""

    memory: MemoryService | None = None
    affinity: AffinityService | None = None
    proactive: ProactiveService | None = None


class ChatPipeline:
    def __init__(
        self,
        *,
        settings: Settings,
        pool: Pool,
        clock: Clock,
        llm: LLMClient,
        prompts: PromptBuilder,
        personas: PersonaRepository,
        moderator: Moderator,
        safety: DefaultSafetyService,
        guard: OutputGuard | None,
        assembler: ContextAssembler,
        modules: PipelineModules,
        jobs: PgJobQueue,
        audit: AuditLogger,
        post_turn_enabled: bool = True,
        on_job_enqueued: Callable[[], None] | None = None,
    ) -> None:
        self._settings = settings
        self._pool = pool
        self._clock = clock
        self._llm = llm
        self._prompts = prompts
        self._personas = personas
        self._moderator = moderator
        self._safety = safety
        self._guard = guard
        self._assembler = assembler
        self._memory = modules.memory if settings.engine_memory_enabled else None
        self._affinity = modules.affinity if settings.engine_affinity_enabled else None
        self._proactive = modules.proactive if settings.engine_proactive_enabled else None
        self._jobs = jobs
        self._audit = audit
        self._post_turn_enabled = post_turn_enabled
        self._on_job_enqueued = on_job_enqueued
        self._background: set[asyncio.Task[Any]] = set()

    @property
    def clock(self) -> Clock:
        return self._clock

    # ------------------------------------------------------------------ 1. 所有者の確認
    async def prepare(self, user: CurrentUser, request: ChatRequest, *, streamed: bool) -> ChatTurn:
        started = time.perf_counter()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_CONVERSATION_SQL, request.conversation_id, user.id, request.character_id)
        if row is None:
            raise not_found("会話が見つかりません。")
        character = character_from_row(row)
        return ChatTurn(
            user_id=user.id,
            character=character,
            persona=self._personas.for_character(character),
            conversation_id=request.conversation_id,
            message=request.message,
            now=self._clock.now(),
            started=started,
            streamed=streamed,
        )

    # ------------------------------------------------------------------ 2〜11
    async def run(self, turn: ChatTurn, emit: Emit) -> None:
        """返答を生成してイベントを emit に渡す。想定外の例外はそのまま送出する（呼び出し側が 500 にする）。"""
        recorder = _EmitRecorder(emit, turn.started)
        await self._audit.log(
            "chat.request",
            user_id=turn.user_id,
            character_id=turn.character.id,
            payload={"conversation_id": turn.conversation_id, "message": turn.message, "streamed": turn.streamed},
            at=turn.now,
        )
        # 3. E6（Gate #1 より前。危機のメッセージに定型の断り文を返さない）
        assessment = self._safety.assess(turn.message)
        if assessment.triggered:
            await self._respond_without_llm(turn, recorder, reason="safety", assessment=assessment)
            return
        # 4. Gate #1（入力）
        input_check = self._moderator.check(turn.message)
        if input_check.flagged:
            await self._flag(
                turn,
                stage="input",
                categories=tuple(input_check.categories),
                matched=tuple(input_check.matched_terms),
                text=turn.message,
            )
            await self._respond_without_llm(turn, recorder, reason="moderated", assessment=None)
            return
        await self._respond_with_llm(turn, recorder)

    async def _respond_with_llm(self, turn: ChatTurn, recorder: _EmitRecorder) -> None:
        persona = turn.persona
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._settings.chat_deadline_seconds
        budget = asyncio.timeout_at(deadline)
        stream: LLMStream | None = None
        flusher = StreamFlusher(
            OutputChecker(self._moderator, self._guard, ng_words=persona.speech.ng_words), persona_name=persona.name
        )
        context: AssembledContext | None = None
        prompt_messages: list[ChatMessage] = []
        try:
            async with budget:
                # 5. 文脈
                context = await self._assembler.assemble(
                    user_id=turn.user_id,
                    character_id=turn.character.id,
                    conversation_id=turn.conversation_id,
                    persona=persona,
                    user_message=turn.message,
                    now=turn.now,
                )
                prompt_messages = self._prompts.chat_messages(
                    persona,
                    history=context.history,
                    user_message=turn.message,
                    now=turn.now,
                    bundle=context.bundle,
                    history_max_chars=self._assembler.budget.history_chars,
                )
                request = LLMRequest(
                    purpose="chat",
                    messages=prompt_messages,
                    temperature=self._settings.llm_temperature,
                    max_tokens=self._settings.llm_max_tokens,
                    hints=MockHints(
                        persona=persona,
                        now=turn.now,
                        user_message=turn.message,
                        history=context.history,
                        context=context.bundle,
                    ),
                )
                # 6. ストリーミング + 文単位のフラッシュ（送る前に出力検査）
                stream = stream_completion(self._llm, request)
                try:
                    async for chunk in stream:
                        for piece in flusher.feed(chunk):
                            recorder.emit(DeltaEvent(piece))
                finally:
                    await stream.aclose()
                final = flusher.finish()
                if final.flag is None and not final.text.strip():
                    raise LLMError("empty reply after cleanup")
        except (LLMError, TimeoutError) as exc:
            if isinstance(exc, TimeoutError) and not budget.expired():
                raise  # 締め切り以外の TimeoutError（DB 接続待ちなど）は 500 として扱う
            await self._llm_error(turn, exc, streamed_chars=len(flusher.emitted))
            recorder.emit(ErrorEvent(503, "llm_unavailable"))
            return

        moderated = False
        reply = final.text
        if final.flag is not None:
            await self._flag(
                turn, stage="output", categories=final.flag.categories, matched=final.flag.matched, text=final.text
            )
            reply = persona.refusal_reply
            moderated = True
            recorder.emit(ReplaceEvent(reply, "moderated"))
        else:
            for piece in final.deltas:
                recorder.emit(DeltaEvent(piece))

        # 7. 保存
        try:
            user_message, character_message = await self._save_messages(turn, reply)
        except ApiError as exc:
            recorder.emit(ErrorEvent(exc.status_code, exc.code, exc.message))
            return
        # 8〜9.
        await self._after_save(turn)
        job_id = await self._enqueue_post_turn(turn)
        # 10.
        memories_used = context.memory_ids if context is not None else []
        response = ChatResponse(
            message_id=character_message.id,
            reply=reply,
            memories_used=memories_used,
            memories_created=[],
            user_message=user_message,
            character_message=character_message,
            moderated=moderated,
            safety=None,
        )
        await self._audit_response(
            turn,
            response,
            recorder=recorder,
            context=context,
            stream=stream,
            prompt_messages=prompt_messages,
            moderation_stage="output" if moderated else None,
            output_flag=final.flag,
            job_id=job_id,
            held=flusher.held,
        )
        recorder.emit(DoneEvent(response))
        # 11. 返答の後
        if self._memory is not None and memories_used:
            self._spawn(self._mark_referenced(self._memory, memories_used, turn.now))

    async def _respond_without_llm(
        self,
        turn: ChatTurn,
        recorder: _EmitRecorder,
        *,
        reason: Literal["moderated", "safety"],
        assessment: SafetyAssessment | None,
    ) -> None:
        """E6 の安全対応 / Gate #1（入力）の定型文。LLM は使わない。"""
        reply = self._safety.build_reply(turn.persona) if reason == "safety" else turn.persona.refusal_reply
        recorder.emit(ReplaceEvent(reply, reason))
        try:
            user_message, character_message = await self._save_messages(
                turn, reply, safety_triggered=reason == "safety"
            )
        except ApiError as exc:
            recorder.emit(ErrorEvent(exc.status_code, exc.code, exc.message))
            return
        safety: SafetyInfo | None = None
        if reason == "safety" and assessment is not None:
            safety = SafetyInfo(
                triggered=True,
                resources=[
                    SafetyResource(name=r.name, phone=r.phone, hours=r.hours, url=r.url)
                    for r in self._safety.resources()
                ],
            )
            await self._audit.log(
                "safety.trigger",
                user_id=turn.user_id,
                character_id=turn.character.id,
                payload={
                    "conversation_id": turn.conversation_id,
                    "user_message_id": user_message.id,
                    "message_id": character_message.id,
                    "categories": list(assessment.categories),
                    "matched": list(assessment.matched),
                    "text": turn.message,
                },
                at=turn.now,
            )
        await self._after_save(turn)
        job_id = await self._enqueue_post_turn(turn)
        response = ChatResponse(
            message_id=character_message.id,
            reply=reply,
            memories_used=[],
            memories_created=[],
            user_message=user_message,
            character_message=character_message,
            moderated=reason == "moderated",
            safety=safety,
        )
        await self._audit_response(
            turn,
            response,
            recorder=recorder,
            context=None,
            stream=None,
            prompt_messages=[],
            moderation_stage="input" if reason == "moderated" else None,
            output_flag=None,
            job_id=job_id,
            held=False,
        )
        recorder.emit(DoneEvent(response))

    # ------------------------------------------------------------------ 保存と後処理
    async def _save_messages(
        self, turn: ChatTurn, reply: str, *, safety_triggered: bool = False
    ) -> tuple[MessageDTO, MessageDTO]:
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                user_row = await conn.fetchrow(_INSERT_USER_MESSAGE_SQL, turn.conversation_id, turn.message, turn.now)
                if user_row is None:  # pragma: no cover - returning 付き insert
                    raise RuntimeError("message insert returned no row")
                character_row = await conn.fetchrow(
                    _INSERT_CHARACTER_MESSAGE_SQL,
                    turn.conversation_id,
                    reply,
                    user_row["created_at"] + REPLY_GAP,
                    safety_triggered,
                )
        except asyncpg.ForeignKeyViolationError as exc:
            # 所有者の確認の後、応答の生成中に会話が削除された（退会・運用者の削除）
            raise not_found("会話が見つかりません。") from exc
        if character_row is None:  # pragma: no cover - returning 付き insert
            raise RuntimeError("message insert returned no row")
        return message_dto(user_row), message_dto(character_row)

    async def _after_save(self, turn: ChatTurn) -> None:
        """自発メッセージへの返信の記録（P4）と、最後に話した日時（A6）。失敗してもチャットは成功させる。"""
        work: list[tuple[str, Coroutine[Any, Any, None]]] = []
        if self._proactive is not None:
            work.append(
                (
                    "proactive.on_user_message",
                    self._proactive.on_user_message(
                        user_id=turn.user_id,
                        character_id=turn.character.id,
                        conversation_id=turn.conversation_id,
                        now=turn.now,
                    ),
                )
            )
        if self._affinity is not None:
            work.append(
                (
                    "affinity.touch_interaction",
                    self._affinity.touch_interaction(
                        user_id=turn.user_id, character_id=turn.character.id, now=turn.now
                    ),
                )
            )
        if not work:
            return
        results = await asyncio.gather(*(coro for _, coro in work), return_exceptions=True)
        for (name, _), result in zip(work, results, strict=True):
            if isinstance(result, BaseException):
                logger.error(
                    "post-save hook failed",
                    extra={
                        "fields": {"hook": name, "conversation_id": str(turn.conversation_id), "error": repr(result)}
                    },
                )

    async def _enqueue_post_turn(self, turn: ChatTurn) -> int | None:
        """返答の後の非同期処理を登録する（失敗してもチャットは成功させる。次のターンでまとめて処理される）。"""
        if not self._post_turn_enabled:
            return None
        payload: dict[str, object] = {
            "conversation_id": str(turn.conversation_id),
            "user_id": str(turn.user_id),
            "character_id": str(turn.character.id),
        }
        delay = timedelta(seconds=self._settings.engine_post_turn_delay_seconds)
        job_id: int | None = None
        try:
            job_id = await self._jobs.enqueue(
                JOB_POST_TURN,
                payload,
                run_at=turn.now + delay,
                dedupe_key=str(turn.conversation_id),
                debounce_max_delay=delay * 6,
            )
            if self._memory is not None:
                await self._jobs.enqueue(
                    JOB_MEMORY_SUMMARIZE, payload, run_at=turn.now + delay, dedupe_key=str(turn.conversation_id)
                )
        except (OSError, TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
            logger.error(
                "failed to enqueue post_turn job",
                extra={"fields": {"conversation_id": str(turn.conversation_id), "error": repr(exc)}},
            )
        if self._on_job_enqueued is not None:
            self._on_job_enqueued()
        return job_id

    async def _mark_referenced(self, memory: MemoryService, memory_ids: list[UUID], now: datetime) -> None:
        try:
            await memory.mark_referenced(memory_ids=memory_ids, now=now)
        except Exception as exc:
            logger.error("mark_referenced failed", extra={"fields": {"error": repr(exc)}})

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def drain(self, timeout_seconds: float = 10.0) -> None:
        """返答の後のバックグラウンド処理（mark_referenced）の完了を待つ（停止時・テスト用）。"""
        if not self._background:
            return
        _, pending = await asyncio.wait(set(self._background), timeout=timeout_seconds)
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # ------------------------------------------------------------------ 監査
    async def _flag(
        self,
        turn: ChatTurn,
        *,
        stage: Literal["input", "output"],
        categories: tuple[str, ...],
        matched: tuple[str, ...],
        text: str,
    ) -> None:
        await self._audit.log(
            "moderation.flag",
            user_id=turn.user_id,
            character_id=turn.character.id,
            payload={
                "stage": stage,
                "context": "chat",
                "conversation_id": turn.conversation_id,
                "categories": list(categories),
                "matched_terms": list(matched),
                "text": text,
            },
            at=turn.now,
        )

    async def _llm_error(self, turn: ChatTurn, exc: Exception, *, streamed_chars: int) -> None:
        if isinstance(exc, LLMError):
            error, status_code, attempts = str(exc), exc.status_code, exc.attempts
        else:
            error = f"deadline exceeded ({self._settings.chat_deadline_seconds:g}s)"
            status_code, attempts = None, None
        await self._audit.log(
            "llm.error",
            user_id=turn.user_id,
            character_id=turn.character.id,
            payload={
                "purpose": "chat",
                "conversation_id": turn.conversation_id,
                "error": error,
                "status_code": status_code,
                "attempts": attempts,
                "streamed_chars": streamed_chars,
                "elapsed_ms": int((time.perf_counter() - turn.started) * 1000),
            },
            at=turn.now,
        )

    async def _audit_response(
        self,
        turn: ChatTurn,
        response: ChatResponse,
        *,
        recorder: _EmitRecorder,
        context: AssembledContext | None,
        stream: LLMStream | None,
        prompt_messages: list[ChatMessage],
        moderation_stage: str | None,
        output_flag: OutputFlag | None,
        job_id: int | None,
        held: bool,
    ) -> None:
        bundle = context.bundle if context is not None else None
        state = bundle.state if bundle is not None else None
        relationship = bundle.relationship if bundle is not None else None
        payload: dict[str, Any] = {
            "conversation_id": turn.conversation_id,
            "user_message_id": response.user_message.id,
            "message_id": response.message_id,
            "reply": response.reply,
            "moderated": response.moderated,
            "moderation_stage": moderation_stage,
            "output_flag_categories": list(output_flag.categories) if output_flag is not None else [],
            "safety": response.safety is not None,
            "streamed": turn.streamed,
            "model": stream.model if stream is not None else None,
            "usage": stream.usage if stream is not None else None,
            "llm_first_chunk_ms": stream.first_chunk_ms if stream is not None else None,
            "llm_latency_ms": stream.latency_ms if stream is not None else None,
            # 送信（認証の後）から最初の文字（delta / replace）を送るまで（E8）
            "ttft_ms": recorder.first_event_ms,
            "latency_ms": int((time.perf_counter() - turn.started) * 1000),
            "output_held": held,
            "memories_used": response.memories_used,
            "memories_created": [],
            "context_budget": bundle.budget_report if bundle is not None else None,
            "context_degraded": list(context.degraded) if context is not None else [],
            "context_timings_ms": context.timings_ms if context is not None else None,
            "history_messages": len(context.history) if context is not None else 0,
            "retrieval_skipped": bundle.memory.retrieval_skipped if bundle is not None else None,
            "state_used": (
                {
                    "activity": state.activity,
                    "status_label": state.status_label,
                    "busyness": state.busyness,
                    "event_id": state.event_id,
                    "event_kind": state.event_kind,
                }
                if state is not None
                else None
            ),
            "stage_used": relationship.stage if relationship is not None else None,
            "call_user_source": context.call_user_source if context is not None else None,
            "promises_in_context": [p.id for p in bundle.memory.promises] if bundle is not None else [],
            "character_memories_used": [m.id for m in bundle.memory.character_memories] if bundle is not None else [],
            "post_turn_job_id": job_id,
            "prompt_chars": sum(len(m["content"]) for m in prompt_messages),
            "persona_key": turn.persona.key,
            "persona_fallback": turn.persona.is_fallback,
            "engine_flags": {
                "memory": self._settings.engine_memory_enabled,
                "calendar": self._settings.engine_calendar_enabled,
                "affinity": self._settings.engine_affinity_enabled,
                "proactive": self._settings.engine_proactive_enabled,
            },
        }
        if self._settings.audit_log_prompts and prompt_messages:
            payload["prompt_messages"] = prompt_messages
        await self._audit.log(
            "chat.response", user_id=turn.user_id, character_id=turn.character.id, payload=payload, at=turn.now
        )


class _EmitRecorder:
    """イベントを渡しつつ、最初の文字を送った時刻（ttft）を記録する。"""

    def __init__(self, emit: Emit, started: float) -> None:
        self._emit = emit
        self._started = started
        self.first_event_ms: int | None = None

    def emit(self, event: PipelineEvent) -> None:
        if self.first_event_ms is None and isinstance(event, DeltaEvent | ReplaceEvent):
            self.first_event_ms = int((time.perf_counter() - self._started) * 1000)
        self._emit(event)
