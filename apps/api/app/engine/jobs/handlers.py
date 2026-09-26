"""返答の後の非同期ジョブ（ENGINE_BRIEF §2.3）。

`post_turn`（dedupe_key = 会話 ID。返答から ENGINE_POST_TURN_DELAY_SECONDS 後にまとめて処理する）:
  1. `conversations.analyzed_until` より新しいターン（ユーザー発言 + 直後のキャラ返答）を古い順に最大
     ENGINE_POST_TURN_MAX_TURNS 件読む（自発メッセージ・挨拶は対にならないので含めない）
  2. memory.process_turns（記憶の抽出・統合・矛盾の解消・約束・キャラ側の記憶）
  3. calendar.sync_promise_events（新しい約束をカレンダーに登録）
  4. affinity.evaluate_turns（Gate #1 で差し止めたターン・E6 の安全対応をしたターンは渡さない）
  5. analyzed_until を処理した最後のキャラ返答の時刻まで進める。残りがあれば続けて実行を依頼する
  各手順は ENGINE_*_ENABLED で無効にできる（評価ハーネスの素の LLM）。途中で失敗した場合、済んだ手順は
  ジョブの payload に記録し、再試行では同じターンの残りの手順だけを行う（記憶の二重登録を防ぐ）。
  最後の試行でも失敗した手順は飛ばして先に進む（同じターンで以後のジョブが永久に止まらないように）。

`memory.summarize`（dedupe_key = 会話 ID）: 中期要約（memory.maybe_summarize）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from app.core.config import Settings
from app.core.db import Pool
from app.core.logging import get_logger
from app.engine.jobs.queue import PgJobQueue
from app.engine.jobs.worker import JobContext, JobRegistry, JobResult, PermanentJobError
from app.engine.types import AffinityService, CalendarService, MemoryService, SafetyService, TurnRecord
from app.services.characters import CHARACTER_COLUMNS, character_from_row
from app.services.moderation import Moderator
from app.services.persona import PersonaRepository

logger = get_logger("engine.jobs.handlers")

JOB_POST_TURN: Final[str] = "post_turn"
JOB_MEMORY_SUMMARIZE: Final[str] = "memory.summarize"

_DONE_KEY: Final[str] = "_done_steps"
_BATCH_KEY: Final[str] = "_batch_until"
_PROMISES_KEY: Final[str] = "_promises"

_CONVERSATION_SQL: Final[str] = f"""
select c.user_id, c.character_id, c.analyzed_until, {", ".join(f"ch.{c.strip()}" for c in CHARACTER_COLUMNS.split(","))}
  from public.conversations c
  join public.characters ch on ch.id = c.character_id
 where c.id = $1
"""  # noqa: S608 - 列名は定数

# 未処理のメッセージ（古い順）。対にするため多めに読む（自発メッセージが挟まる場合もある）
_MESSAGES_SQL: Final[str] = """
select id, sender_type, body, created_at, is_proactive, safety_triggered
  from public.messages
 where conversation_id = $1
   and created_at > coalesce($2::timestamptz, '-infinity'::timestamptz)
   and ($3::timestamptz is null or created_at <= $3::timestamptz)
 order by created_at asc
 limit $4
"""

_ADVANCE_SQL: Final[str] = """
update public.conversations
   set analyzed_until = greatest(coalesce(analyzed_until, '-infinity'::timestamptz), $2)
 where id = $1
"""


@dataclass(frozen=True, slots=True)
class LoadedTurns:
    turns: tuple[TurnRecord, ...]
    last_reply_at: datetime | None  # 処理したターンの最後のキャラ返答の時刻（analyzed_until の新しい値）
    has_more: bool


def _as_uuid(value: object, name: str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except ValueError as exc:
        raise PermanentJobError(f"invalid {name}: {value!r}") from exc


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class EngineJobHandlers:
    """post_turn / memory.summarize のハンドラ（モジュールは Protocol 経由で受け取る。無効なら None）。"""

    def __init__(
        self,
        *,
        settings: Settings,
        pool: Pool,
        queue: PgJobQueue,
        personas: PersonaRepository,
        moderator: Moderator,
        safety: SafetyService,
        memory: MemoryService | None,
        calendar: CalendarService | None,
        affinity: AffinityService | None,
    ) -> None:
        self._settings = settings
        self._pool = pool
        self._queue = queue
        self._personas = personas
        self._moderator = moderator
        self._safety = safety
        self._memory = memory
        self._calendar = calendar
        self._affinity = affinity

    def register(self, registry: JobRegistry) -> None:
        registry.register(JOB_POST_TURN, self.post_turn)
        registry.register(JOB_MEMORY_SUMMARIZE, self.memory_summarize)

    @property
    def memory_enabled(self) -> bool:
        return self._settings.engine_memory_enabled and self._memory is not None

    @property
    def calendar_enabled(self) -> bool:
        return self._settings.engine_calendar_enabled and self._calendar is not None

    @property
    def affinity_enabled(self) -> bool:
        return self._settings.engine_affinity_enabled and self._affinity is not None

    @property
    def post_turn_needed(self) -> bool:
        """post_turn で行う手順が1つでもあるか（すべて無効ならジョブを登録しない）。"""
        return self.memory_enabled or self.affinity_enabled

    # ------------------------------------------------------------------ post_turn
    async def post_turn(self, ctx: JobContext) -> JobResult:
        payload = ctx.payload
        conversation_id = _as_uuid(payload.get("conversation_id"), "conversation_id")
        now = ctx.now
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_CONVERSATION_SQL, conversation_id)
        if row is None:
            # 会話が削除された（退会など）→ 何もしない
            return JobResult(detail={"skipped": "conversation_not_found"})
        user_id: UUID = row["user_id"]
        character = character_from_row(row)
        persona = self._personas.for_character(character)

        loaded = await self._load_turns(
            conversation_id,
            user_id=user_id,
            character_id=character.id,
            after=row["analyzed_until"],
            until=_parse_time(payload.get(_BATCH_KEY)),
            refusal_reply=persona.refusal_reply,
        )
        if not loaded.turns or loaded.last_reply_at is None:
            return JobResult(detail={"turns": 0})
        turns = loaded.turns
        done: list[str] = list(payload.get(_DONE_KEY) or [])
        last_attempt = ctx.job.attempts >= ctx.job.max_attempts
        errors: dict[str, str] = {}
        checkpoint: dict[str, object] = {_BATCH_KEY: loaded.last_reply_at.isoformat()}
        promise_ids = [_as_uuid(p, "promise_id") for p in payload.get(_PROMISES_KEY) or []]

        async def mark(step: str) -> None:
            done.append(step)
            checkpoint[_DONE_KEY] = done
            await self._queue.update_payload(ctx.job, checkpoint)

        # 1. 記憶
        if self.memory_enabled and "memory" not in done and self._memory is not None:
            try:
                result = await self._memory.process_turns(
                    user_id=user_id,
                    character_id=character.id,
                    conversation_id=conversation_id,
                    turns=turns,
                    now=now,
                )
                promise_ids = list(result.promises_created)
                checkpoint[_PROMISES_KEY] = [str(p) for p in promise_ids]
                await mark("memory")
            except Exception as exc:
                if not last_attempt:
                    raise
                errors["memory"] = repr(exc)
        # 2. 約束の予定化（C8）
        if self.calendar_enabled and promise_ids and "calendar" not in done and self._calendar is not None:
            try:
                await self._calendar.sync_promise_events(
                    user_id=user_id, character_id=character.id, promise_ids=promise_ids, now=now
                )
                await mark("calendar")
            except Exception as exc:
                if not last_attempt:
                    raise
                errors["calendar"] = repr(exc)
        # 3. 好感度（差し止め・安全対応のターンは評価しない）
        eligible = [t for t in turns if not t.moderated and not t.safety_triggered]
        if self.affinity_enabled and eligible and "affinity" not in done and self._affinity is not None:
            try:
                await self._affinity.evaluate_turns(user_id=user_id, character_id=character.id, turns=eligible, now=now)
                await mark("affinity")
            except Exception as exc:
                if not last_attempt:
                    raise
                errors["affinity"] = repr(exc)
        # 4. 処理済みの位置を進める
        async with self._pool.acquire() as conn:
            await conn.execute(_ADVANCE_SQL, conversation_id, loaded.last_reply_at)
        if errors:
            logger.error(
                "post_turn gave up some steps on the last attempt",
                extra={"fields": {"conversation_id": str(conversation_id), "errors": errors}},
            )
        detail: dict[str, Any] = {
            "conversation_id": str(conversation_id),
            "turns": len(turns),
            "affinity_turns": len(eligible),
            "promises": len(promise_ids),
            "skipped_steps": sorted(errors),
        }
        return JobResult(rerun_at=now if loaded.has_more else None, detail=detail)

    async def _load_turns(
        self,
        conversation_id: UUID,
        *,
        user_id: UUID,
        character_id: UUID,
        after: datetime | None,
        until: datetime | None,
        refusal_reply: str,
    ) -> LoadedTurns:
        max_turns = self._settings.engine_post_turn_max_turns
        # ターン数の上限 + 自発メッセージ・挨拶の分の余裕（足りなければ has_more で続きを処理する）
        limit = max_turns * 2 + 20
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_MESSAGES_SQL, conversation_id, after, until, limit)
        turns: list[TurnRecord] = []
        last_reply_at: datetime | None = None
        pending_user: Any = None
        truncated = False
        for row in rows:
            if row["sender_type"] == "user":
                pending_user = row
                continue
            if row["is_proactive"] or pending_user is None:
                continue
            if len(turns) >= max_turns:
                truncated = True
                break
            user_text: str = pending_user["body"]
            reply_text: str = row["body"]
            turns.append(
                TurnRecord(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    character_id=character_id,
                    user_message_id=pending_user["id"],
                    character_message_id=row["id"],
                    user_text=user_text,
                    reply_text=reply_text,
                    occurred_at=pending_user["created_at"],
                    # Gate #1 の判定は列として持たない。決定的な照合なので保存済みの本文にもう一度かける
                    moderated=self._moderator.check(user_text).flagged or reply_text == refusal_reply,
                    # E6: 保存時の印（messages.safety_triggered）が正。印の無い古い行は本文にもう一度かける
                    safety_triggered=bool(row["safety_triggered"]) or self._safety.assess(user_text).triggered,
                )
            )
            last_reply_at = row["created_at"]
            pending_user = None
        # 上限で打ち切った / 読み込んだ件数が上限に達した（続きがあるかもしれない）→ 続けて実行する
        has_more = truncated or len(rows) >= limit
        return LoadedTurns(turns=tuple(turns), last_reply_at=last_reply_at, has_more=has_more)

    # ------------------------------------------------------------------ memory.summarize
    async def memory_summarize(self, ctx: JobContext) -> JobResult:
        if not self.memory_enabled or self._memory is None:
            return JobResult(detail={"skipped": "memory_disabled"})
        payload = ctx.payload
        await self._memory.maybe_summarize(
            conversation_id=_as_uuid(payload.get("conversation_id"), "conversation_id"),
            user_id=_as_uuid(payload.get("user_id"), "user_id"),
            character_id=_as_uuid(payload.get("character_id"), "character_id"),
            now=ctx.now,
        )
        return JobResult()
