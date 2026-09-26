"""自発メッセージ（Proactive Messenger, 仕様 §7。app.engine.types.ProactiveService の実装）。

scan(now)（スケジューラ proactive.scan。10 分ごと）:
  1. 会話があり（ユーザーが 1 回以上発言している）、退会しておらず、全体・キャラ別の設定で止めていないペアを集める
  2. ユーザー単位の制限（送らない時間帯・1 日の上限・P4・間隔）→ ペア単位の上限（段階 × ペルソナの頻度）
  3. きっかけ（約束の期日・予定の終了・季節の行事・しばらく話していない・フィードの投稿）→ スコア → ユーザーごとに 1 件
  4. LLM（proactive_message）で文面 → Gate #1（Moderator）+ OutputGuard（E2 / E3）+ 責める言い方の検査
     → 引っかかったら送らない（そのきっかけは使い切り。監査 moderation.flag / proactive.dropped）
  5. 1 トランザクションで proactive_messages（unique = 冪等）と messages（is_proactive, created_at = now）を保存
     → 監査 proactive.send → 約束なら MemoryService.mark_promise_mentioned

他のモジュールには types.py の Protocol（CalendarService / MemoryService / AffinityService / OutputGuard）
だけで依存する。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final
from uuid import UUID

import asyncpg

from app.core.db import Pool
from app.core.logging import get_logger
from app.engine.affinity.guidance import stage_proactive_frequency
from app.engine.affinity.model import cap_stage, max_stage_of
from app.engine.affinity.ports import AuditLog
from app.engine.proactive.config import ProactiveConfig
from app.engine.proactive.message import (
    PURPOSE,
    RecentMessage,
    call_user_for,
    load_template,
    mock_context,
    render_messages,
)
from app.engine.proactive.rules import (
    PAIR_DAILY_LIMIT,
    PairContext,
    ScoredCandidate,
    TriggerCandidate,
    calendar_candidate,
    feed_post_candidate,
    fingerprint,
    guilt_trip_phrases,
    inactivity_candidate,
    inactivity_days,
    paid_notice_candidate,
    pair_blocked_reason,
    pair_daily_limit,
    persona_frequency,
    persona_triggers,
    promise_candidates,
    score,
    seasonal_candidates,
    user_blocked_reason,
    within_trigger_hours,
)
from app.engine.types import (
    STAGES,
    AffinityService,
    CalendarService,
    CharacterStateSnapshot,
    MemoryContext,
    MemoryService,
    OutputGuard,
    RelationshipGuidance,
    WorldState,
    to_jst,
)
from app.services.llm import LLMClient, LLMError, LLMRequest, clean_reply
from app.services.moderation import Moderator
from app.services.persona import Persona, PersonaRepository
from app.services.types import CharacterRecord

logger = get_logger("engine.proactive")

_JST_DAY: Final[timedelta] = timedelta(days=1)

# 会話があるペア（ユーザーが 1 回以上発言し、dormant_days 以内に話している）。設定で止めているペアは除く
_PAIRS_SQL: Final[str] = """
select c.id as conversation_id, c.user_id, c.character_id,
       ch.id, ch.handle, ch.name, ch.avatar_url, ch.bio, ch.persona_key, ch.system_prompt, ch.is_active,
       coalesce(a.stage, 'acquaintance') as stage,
       coalesce(lm.is_proactive, false) as last_is_proactive, lm.created_at as last_message_at,
       lu.created_at as last_user_message_at,
       gs.quiet_start, gs.quiet_end
  from public.conversations c
  join public.characters ch on ch.id = c.character_id and ch.is_active
  join public.profiles p on p.id = c.user_id and p.deleted_at is null
  left join public.affinity_states a on a.user_id = c.user_id and a.character_id = c.character_id
  left join public.proactive_settings gs on gs.user_id = c.user_id and gs.character_id is null
  left join public.proactive_settings cs on cs.user_id = c.user_id and cs.character_id = c.character_id
  join lateral (
    select m.created_at from public.messages m
     where m.conversation_id = c.id and m.sender_type = 'user' and m.created_at <= $1
     order by m.created_at desc limit 1
  ) lu on true
  left join lateral (
    select m.is_proactive, m.created_at from public.messages m
     where m.conversation_id = c.id and m.created_at <= $1
     order by m.created_at desc limit 1
  ) lm on true
 where coalesce(gs.enabled, true) and coalesce(cs.enabled, true)
   and lu.created_at > $1 - make_interval(days => $2)
   and ($3::uuid[] is null or c.user_id = any($3::uuid[]))
 order by c.user_id, c.character_id
"""

# 送信済み（message_id がある = 実際に送った。生成後に差し止めた記録は数えない）
_SENT_COUNTS_SQL: Final[str] = """
select user_id, character_id,
       count(*) filter (where sent_at >= $2) as sent_today,
       max(sent_at) as last_sent,
       count(*) filter (where trigger = 'paid_notice' and sent_at >= $3) as paid_week
  from public.proactive_messages
 where user_id = any($1::uuid[]) and message_id is not null and sent_at <= $4 and sent_at >= least($2, $3)
 group by user_id, character_id
"""

_USED_REFS_SQL: Final[str] = """
select character_id, trigger, trigger_ref from public.proactive_messages
 where user_id = $1 and trigger_ref = any($2::text[])
"""

_RECENT_POST_SQL: Final[str] = """
select id, caption from public.posts
 where character_id = $1 and is_paid = $4 and published_at <= $2 and published_at > $2 - $3::interval
 order by published_at desc
 limit 1
"""

_RECENT_MESSAGES_SQL: Final[str] = """
select sender_type, body, created_at from public.messages
 where conversation_id = $1 and created_at <= $2
 order by created_at desc
 limit $3
"""

_LOCK_CONVERSATION_SQL: Final[str] = """
select id from public.conversations where id = $1 and user_id = $2 and character_id = $3 for update
"""

_LAST_MESSAGE_SQL: Final[str] = """
select is_proactive from public.messages where conversation_id = $1 and created_at <= $2
 order by created_at desc limit 1
"""

_COUNT_SENT_SQL: Final[str] = """
select count(*) filter (where sent_at >= $3) as user_count,
       count(*) filter (where sent_at >= $3 and character_id = $2) as pair_count,
       count(*) filter (where trigger = 'paid_notice') as paid_week
  from public.proactive_messages
 where user_id = $1 and message_id is not null and sent_at >= least($3, $5) and sent_at <= $4
"""

_INSERT_PROACTIVE_SQL: Final[str] = """
insert into public.proactive_messages (user_id, character_id, conversation_id, trigger, trigger_ref, sent_at, meta)
values ($1, $2, $3, $4, $5, $6, $7)
on conflict (user_id, character_id, trigger, trigger_ref) do nothing
returning id
"""

_INSERT_MESSAGE_SQL: Final[str] = """
insert into public.messages (conversation_id, sender_type, body, is_proactive, created_at)
values ($1, 'character', $2, true, $3)
returning id
"""

_LINK_MESSAGE_SQL: Final[str] = "update public.proactive_messages set message_id = $2 where id = $1"

_MARK_REPLIED_SQL: Final[str] = """
update public.proactive_messages set replied_at = $4
 where user_id = $1 and character_id = $2 and conversation_id = $3
   and replied_at is null and message_id is not null and sent_at <= $4
"""


@dataclass(slots=True)
class _CharacterView:
    record: CharacterRecord
    persona: Persona
    state: CharacterStateSnapshot | None = None
    earlier: tuple[CharacterStateSnapshot, ...] = ()
    posts_checked: bool = False
    free_post: tuple[UUID, str | None] | None = None
    paid_checked: bool = False
    paid_post: UUID | None = None


@dataclass(slots=True)
class _ScanStats:
    pairs: int = 0
    blocked: dict[str, int] = field(default_factory=dict)
    candidates: int = 0
    sent: int = 0
    dropped: int = 0
    errors: int = 0

    def block(self, reason: str) -> None:
        self.blocked[reason] = self.blocked.get(reason, 0) + 1


def _character_from_row(row: asyncpg.Record) -> CharacterRecord:
    return CharacterRecord(
        id=row["id"],
        handle=row["handle"],
        name=row["name"],
        avatar_url=row["avatar_url"],
        bio=row["bio"],
        persona_key=row["persona_key"],
        system_prompt=row["system_prompt"],
        is_active=row["is_active"],
    )


def jst_day_start(now: datetime) -> datetime:
    local = to_jst(now)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


class ProactiveMessenger:
    """自発メッセージの判定と送信。コンストラクタで依存を受け取り、時刻（now）は各メソッドの引数で受け取る。

    calendar / memory / affinity は機能フラグで無効なら None（その場合: 予定・季節・約束のきっかけは使わない /
    段階は「友達」として扱う）。
    """

    def __init__(
        self,
        *,
        pool: Pool,
        llm: LLMClient,
        audit: AuditLog,
        personas: PersonaRepository,
        moderator: Moderator,
        guard: OutputGuard,
        prompts_dir: Path,
        calendar: CalendarService | None = None,
        memory: MemoryService | None = None,
        affinity: AffinityService | None = None,
        config: ProactiveConfig | None = None,
    ) -> None:
        self._pool = pool
        self._llm = llm
        self._audit = audit
        self._personas = personas
        self._moderator = moderator
        self._guard = guard
        self._calendar = calendar
        self._memory = memory
        self._affinity = affinity
        self._config = config or ProactiveConfig()
        self._template = load_template(prompts_dir)

    @property
    def config(self) -> ProactiveConfig:
        return self._config

    # ------------------------------------------------------------------
    # P4: ユーザーの発言で返信済みにする
    # ------------------------------------------------------------------

    async def on_user_message(self, *, user_id: UUID, character_id: UUID, conversation_id: UUID, now: datetime) -> None:
        await self._pool.execute(_MARK_REPLIED_SQL, user_id, character_id, conversation_id, now)

    # ------------------------------------------------------------------
    # 走査
    # ------------------------------------------------------------------

    async def scan(self, *, now: datetime, user_ids: Sequence[UUID] | None = None) -> int:
        """送るべき自発メッセージを判定して送る。送った件数を返す。

        user_ids を渡すとそのユーザーだけを対象にする（評価ハーネス・テスト用。本番のスケジューラは渡さない）。
        """
        config = self._config
        stats = _ScanStats()
        pairs, characters = await self._load_pairs(now, user_ids)
        stats.pairs = len(pairs)
        world = self._calendar.world_state(now) if self._calendar is not None else None
        best_by_user: dict[UUID, ScoredCandidate] = {}
        for pair in pairs:
            try:
                best = await self._best_candidate(pair, characters[pair.character_id], world, now, stats)
            except (asyncpg.PostgresError, OSError, TimeoutError):
                # 1 ペアの失敗（カレンダー・記憶の取得など）で走査全体を止めない
                stats.errors += 1
                logger.exception(
                    "proactive: candidate evaluation failed",
                    extra={"fields": {"user_id": str(pair.user_id), "character_id": str(pair.character_id)}},
                )
                continue
            if best is None:
                continue
            current = best_by_user.get(pair.user_id)
            if current is None or best.score > current.score:
                best_by_user[pair.user_id] = best

        semaphore = asyncio.Semaphore(max(config.max_concurrency, 1))

        async def run(plan: ScoredCandidate) -> str:
            async with semaphore:
                try:
                    return await self._send(plan, characters[plan.pair.character_id], world, now)
                except (asyncpg.PostgresError, OSError, TimeoutError):
                    logger.exception(
                        "proactive send failed",
                        extra={"fields": {"user_id": str(plan.pair.user_id), "trigger": plan.candidate.trigger}},
                    )
                    return "error"

        outcomes = await asyncio.gather(*(run(plan) for plan in best_by_user.values()))
        stats.sent = outcomes.count("sent")
        stats.dropped = outcomes.count("dropped")
        stats.errors = outcomes.count("error")
        logger.info(
            "proactive scan finished",
            extra={
                "fields": {
                    "now": now.isoformat(),
                    "pairs": stats.pairs,
                    "candidates": stats.candidates,
                    "sent": stats.sent,
                    "dropped": stats.dropped,
                    "errors": stats.errors,
                    "blocked": stats.blocked,
                }
            },
        )
        return stats.sent

    async def _best_candidate(
        self,
        pair: PairContext,
        view: _CharacterView,
        world: WorldState | None,
        now: datetime,
        stats: _ScanStats,
    ) -> ScoredCandidate | None:
        """ペアで送るべききっかけのうち、スコアが最も高いもの（送れない・送るものが無ければ None）。"""
        config = self._config
        persona = view.persona
        frequency = persona_frequency(persona, config)
        reason = user_blocked_reason(pair, now, config)
        if reason is not None:
            stats.block(reason)
            return None
        await self._ensure_character_state(view, now)
        if view.state is not None and view.state.busyness >= 2:
            stats.block("character_busy")  # 手が離せない・寝ている時間は送らない（キャラの一貫性）
            return None
        pair_reason = pair_blocked_reason(pair, now, frequency, config)
        candidates = await self._candidates(pair, view, world, now, pair_reason=pair_reason)
        hour = to_jst(now).hour
        candidates = [c for c in candidates if within_trigger_hours(c.trigger, hour, config)]
        candidates = await self._unused(pair, candidates)
        if not candidates:
            stats.block(pair_reason or "no_trigger")
            return None
        stage_frequency = stage_proactive_frequency(persona, pair.stage)
        scored = [ScoredCandidate(pair, c, score(c.trigger, stage_frequency, frequency, config)) for c in candidates]
        scored = [s for s in scored if s.score >= config.min_score]
        stats.candidates += len(scored)
        if not scored:
            stats.block("low_score")
            return None
        return max(scored, key=lambda s: s.score)

    # ------------------------------------------------------------------
    # 対象のペアと、送信済みの数
    # ------------------------------------------------------------------

    async def _load_pairs(
        self, now: datetime, user_ids: Sequence[UUID] | None
    ) -> tuple[list[PairContext], dict[UUID, _CharacterView]]:
        config = self._config
        day_start = jst_day_start(now)
        week_start = now - timedelta(days=7)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                _PAIRS_SQL, now, config.dormant_days, list(user_ids) if user_ids is not None else None
            )
            found = sorted({r["user_id"] for r in rows})
            counts = await conn.fetch(_SENT_COUNTS_SQL, found, day_start, week_start, now) if found else []
        per_pair: dict[tuple[UUID, UUID], asyncpg.Record] = {(c["user_id"], c["character_id"]): c for c in counts}
        user_today: dict[UUID, int] = {}
        user_last: dict[UUID, datetime] = {}
        user_paid: dict[UUID, int] = {}
        for c in counts:
            uid = c["user_id"]
            user_today[uid] = user_today.get(uid, 0) + int(c["sent_today"])
            user_paid[uid] = user_paid.get(uid, 0) + int(c["paid_week"])
            last = c["last_sent"]
            if last is not None and (uid not in user_last or last > user_last[uid]):
                user_last[uid] = last
        characters: dict[UUID, _CharacterView] = {}
        pairs: list[PairContext] = []
        for row in rows:
            character_id: UUID = row["character_id"]
            if character_id not in characters:
                record = _character_from_row(row)
                characters[character_id] = _CharacterView(record=record, persona=self._personas.for_character(record))
            pair_counts = per_pair.get((row["user_id"], character_id))
            # ペルソナの段階の上限（max_stage。人妻は close まで）を超える段階では判定しない
            stage = (
                cap_stage(row["stage"], max_stage_of(characters[character_id].persona))
                if self._affinity is not None
                else "friend"
            )
            pairs.append(
                PairContext(
                    user_id=row["user_id"],
                    character_id=character_id,
                    conversation_id=row["conversation_id"],
                    stage=stage if stage in STAGES else "acquaintance",
                    last_message_is_proactive=bool(row["last_is_proactive"]),
                    last_message_at=row["last_message_at"],
                    last_user_message_at=row["last_user_message_at"],
                    quiet_start=row["quiet_start"] if row["quiet_start"] is not None else config.quiet_start_default,
                    quiet_end=row["quiet_end"] if row["quiet_end"] is not None else config.quiet_end_default,
                    sent_today_user=user_today.get(row["user_id"], 0),
                    sent_today_pair=int(pair_counts["sent_today"]) if pair_counts is not None else 0,
                    last_sent_user=user_last.get(row["user_id"]),
                    last_sent_pair=pair_counts["last_sent"] if pair_counts is not None else None,
                    paid_notices_week_user=user_paid.get(row["user_id"], 0),
                )
            )
        return pairs, characters

    async def _ensure_character_state(self, view: _CharacterView, now: datetime) -> None:
        """キャラの今の状態と、少し前の状態（予定の終了の判定用）。1 回の走査でキャラごとに 1 回だけ取得する。"""
        if self._calendar is None or view.state is not None:
            return
        calendar = self._calendar
        character_id = view.record.id
        times = [now - timedelta(minutes=m) for m in self._config.calendar_lookback_minutes]
        snapshots = await asyncio.gather(
            calendar.current_state(character_id=character_id, now=now),
            *(calendar.current_state(character_id=character_id, now=t) for t in times),
        )
        view.state = snapshots[0]
        view.earlier = tuple(snapshots[1:])

    async def _recent_post(self, character_id: UUID, now: datetime, *, paid: bool) -> asyncpg.Record | None:
        window = self._config.paid_notice_window if paid else self._config.feed_post_window
        return await self._pool.fetchrow(_RECENT_POST_SQL, character_id, now, window, paid)

    # ------------------------------------------------------------------
    # きっかけ
    # ------------------------------------------------------------------

    async def _candidates(
        self,
        pair: PairContext,
        view: _CharacterView,
        world: WorldState | None,
        now: datetime,
        *,
        pair_reason: str | None,
    ) -> list[TriggerCandidate]:
        """pair_reason: ペアの上限の理由（None = 上限なし / pair_daily_limit = 約束の期日と有料投稿のお知らせだけ /
        pair_gap = 有料投稿のお知らせだけ）。"""
        config = self._config
        persona = view.persona
        allowed = set(persona_triggers(persona))
        result: list[TriggerCandidate] = []
        if pair_reason in (None, PAIR_DAILY_LIMIT) and "promise_due" in allowed and self._memory is not None:
            promises = await self._memory.due_promises(
                user_id=pair.user_id, character_id=pair.character_id, now=now, window=_JST_DAY
            )
            result.extend(promise_candidates(promises, now, config))
        if pair_reason is None:
            if "calendar_event" in allowed and view.state is not None:
                recently = (
                    pair.last_user_message_at is not None
                    and now - pair.last_user_message_at <= config.recent_user_message_window
                )
                candidate = calendar_candidate(view.state, view.earlier, user_recently_messaged=recently, config=config)
                if candidate is not None:
                    result.append(candidate)
            if "seasonal" in allowed and world is not None:
                result.extend(seasonal_candidates(world, persona))
            if "inactivity" in allowed:
                candidate = inactivity_candidate(pair, now, inactivity_days(persona, config), config)
                if candidate is not None:
                    result.append(candidate)
            if "feed_post" in allowed:
                if not view.posts_checked:
                    row = await self._recent_post(view.record.id, now, paid=False)
                    view.free_post = (row["id"], row["caption"]) if row is not None else None
                    view.posts_checked = True
                if view.free_post is not None:
                    candidate = feed_post_candidate(pair, view.free_post[0], view.free_post[1], config)
                    if candidate is not None:
                        result.append(candidate)
        if config.paid_notice_enabled and pair.paid_notices_week_user < config.paid_notice_weekly_limit:
            if not view.paid_checked:
                row = await self._recent_post(view.record.id, now, paid=True)
                view.paid_post = row["id"] if row is not None else None
                view.paid_checked = True
            candidate = paid_notice_candidate(view.paid_post)
            if candidate is not None:
                result.append(candidate)
        return result

    async def _unused(self, pair: PairContext, candidates: Sequence[TriggerCandidate]) -> list[TriggerCandidate]:
        """既に送った（または差し止めた）きっかけを除く。季節の行事は 1 ユーザーに全キャラ合わせて 1 回。"""
        if not candidates:
            return []
        refs = sorted({c.trigger_ref for c in candidates})
        rows = await self._pool.fetch(_USED_REFS_SQL, pair.user_id, refs)
        used_pair = {(r["trigger"], r["trigger_ref"]) for r in rows if r["character_id"] == pair.character_id}
        used_user_seasonal = {r["trigger_ref"] for r in rows if r["trigger"] == "seasonal"}
        result: list[TriggerCandidate] = []
        for c in candidates:
            if (c.trigger, c.trigger_ref) in used_pair:
                continue
            if c.trigger == "seasonal" and c.trigger_ref in used_user_seasonal:
                continue
            result.append(c)
        return result

    # ------------------------------------------------------------------
    # 生成・検査・送信
    # ------------------------------------------------------------------

    async def _context(
        self, plan: ScoredCandidate, now: datetime
    ) -> tuple[RelationshipGuidance | None, MemoryContext | None, list[RecentMessage]]:
        pair = plan.pair
        guidance: RelationshipGuidance | None = None
        if self._affinity is not None:
            guidance = await self._affinity.guidance(user_id=pair.user_id, character_id=pair.character_id, now=now)
        memory_context: MemoryContext | None = None
        if self._memory is not None:
            memory_context = await self._memory_context(plan, now)
        rows = await self._pool.fetch(_RECENT_MESSAGES_SQL, pair.conversation_id, now, self._config.recent_messages)
        recent = [RecentMessage(r["sender_type"], r["body"], r["created_at"]) for r in reversed(rows)]
        return guidance, memory_context, recent

    async def _memory_context(self, plan: ScoredCandidate, now: datetime) -> MemoryContext | None:
        memory = self._memory
        if memory is None:
            return None
        pair = plan.pair
        query = plan.candidate.description
        try:
            async with asyncio.timeout(self._config.memory_timeout_seconds):
                embedding = await memory.embed_query(
                    query, user_id=pair.user_id, character_id=pair.character_id, conversation_id=pair.conversation_id
                )
                return await memory.retrieve_context(
                    user_id=pair.user_id,
                    character_id=pair.character_id,
                    query_text=query,
                    query_embedding=embedding,
                    now=now,
                )
        except (TimeoutError, asyncpg.PostgresError, OSError):
            logger.warning(
                "proactive: memory context unavailable",
                extra={"fields": {"user_id": str(pair.user_id), "character_id": str(pair.character_id)}},
            )
            return None

    async def _send(self, plan: ScoredCandidate, view: _CharacterView, world: WorldState | None, now: datetime) -> str:
        pair, candidate = plan.pair, plan.candidate
        persona = view.persona
        config = self._config
        guidance, memory_context, recent = await self._context(plan, now)
        call_user = call_user_for(persona, guidance, pair.stage)
        messages = render_messages(
            self._template,
            persona=persona,
            guidance=guidance,
            call_user=call_user,
            world=world,
            state=view.state,
            candidate=candidate,
            memory=memory_context,
            recent=recent,
            now=now,
        )
        request = LLMRequest(
            purpose=PURPOSE,
            messages=messages,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            model=config.model,
            mock_context=mock_context(
                persona=persona, stage=pair.stage, call_user=call_user, candidate=candidate, state=view.state
            ),
        )
        base_payload: dict[str, Any] = {
            "purpose": PURPOSE,
            "trigger": candidate.trigger,
            "trigger_ref": candidate.trigger_ref,
            "conversation_id": pair.conversation_id,
            "relationship_stage": pair.stage,
            "score": plan.score,
        }
        try:
            result = await self._llm.complete(request)
        except LLMError as exc:
            await self._audit.log(
                "llm.error",
                user_id=pair.user_id,
                character_id=pair.character_id,
                payload={**base_payload, "error": str(exc)[:500], "status_code": exc.status_code},
                at=now,
            )
            return "error"
        text = clean_reply(result.text, persona.name, max_chars=config.max_message_chars)
        usage_payload = {"model": result.model, "usage": result.usage, "latency_ms": result.latency_ms}

        # --- 出力の検査（Gate #1 + OutputGuard(E2 / E3) + 責める言い方）→ 引っかかったら送らない
        moderation = self._moderator.check(text, extra_ng_words=persona.speech.ng_words, block_links=True)
        guard = self._guard.check(text)
        guilt = guilt_trip_phrases(text)
        if not text or moderation.flagged or guard.flagged or guilt:
            categories = [*moderation.categories, *guard.categories, *(["guilt_trip"] if guilt else [])]
            matched = [*moderation.matched_terms, *guard.matched, *guilt]
            if moderation.flagged or guard.flagged:
                await self._audit.log(
                    "moderation.flag",
                    user_id=pair.user_id,
                    character_id=pair.character_id,
                    payload={
                        "stage": "proactive",
                        "categories": categories,
                        "matched_terms": matched,
                        "text": text,
                        **base_payload,
                    },
                    at=now,
                )
            await self._record_drop(plan, now, reason="empty" if not text else ",".join(categories))
            await self._audit.log(
                "proactive.dropped",
                user_id=pair.user_id,
                character_id=pair.character_id,
                payload={
                    **base_payload,
                    **usage_payload,
                    "reason": "empty" if not text else "output_check",
                    "categories": categories,
                    "matched_terms": matched,
                    "text_hash": fingerprint(text),
                },
                at=now,
            )
            return "dropped"

        # --- 保存（冪等: unique (user, character, trigger, trigger_ref)）
        day_start = jst_day_start(now)
        meta = {
            "score": plan.score,
            "stage": pair.stage,
            "model": result.model,
            "context": {k: v for k, v in candidate.context.items() if isinstance(v, str | int | float | bool)},
        }
        async with self._pool.acquire() as conn, conn.transaction():
            locked = await conn.fetchval(_LOCK_CONVERSATION_SQL, pair.conversation_id, pair.user_id, pair.character_id)
            if locked is None:
                return "skipped"
            last_is_proactive = await conn.fetchval(_LAST_MESSAGE_SQL, pair.conversation_id, now)
            if last_is_proactive:
                return "skipped"  # P4（走査の後に別の経路で送られた）
            counts = await conn.fetchrow(
                _COUNT_SENT_SQL, pair.user_id, pair.character_id, day_start, now, now - timedelta(days=7)
            )
            user_count = int(counts["user_count"]) if counts is not None else 0
            pair_count = int(counts["pair_count"]) if counts is not None else 0
            paid_week = int(counts["paid_week"]) if counts is not None else 0
            frequency = persona_frequency(persona, config)
            if user_count >= config.per_user_daily_limit:
                return "skipped"
            if not candidate.exempt_pair_limit and pair_count >= pair_daily_limit(pair.stage, frequency, config):
                return "skipped"
            if candidate.trigger == "paid_notice" and paid_week >= config.paid_notice_weekly_limit:
                return "skipped"
            proactive_id = await conn.fetchval(
                _INSERT_PROACTIVE_SQL,
                pair.user_id,
                pair.character_id,
                pair.conversation_id,
                candidate.trigger,
                candidate.trigger_ref,
                now,
                meta,
            )
            if proactive_id is None:
                return "skipped"  # 同じきっかけで既に送った
            message_id = await conn.fetchval(_INSERT_MESSAGE_SQL, pair.conversation_id, text, now)
            await conn.execute(_LINK_MESSAGE_SQL, proactive_id, message_id)

        await self._audit.log(
            "proactive.send",
            user_id=pair.user_id,
            character_id=pair.character_id,
            payload={
                **base_payload,
                **usage_payload,
                "proactive_message_id": proactive_id,
                "message_id": message_id,
                "promise_id": candidate.promise_id,
                "text": text,
            },
            at=now,
        )
        if candidate.promise_id is not None and self._memory is not None:
            await self._memory.mark_promise_mentioned(promise_id=candidate.promise_id, now=now)
        return "sent"

    async def _record_drop(self, plan: ScoredCandidate, now: datetime, *, reason: str) -> None:
        """差し止めたきっかけを使い切りにする（message_id なし。上限の数には入れない）。同じ文脈で生成し直さない。"""
        pair, candidate = plan.pair, plan.candidate
        await self._pool.execute(
            _INSERT_PROACTIVE_SQL,
            pair.user_id,
            pair.character_id,
            pair.conversation_id,
            candidate.trigger,
            candidate.trigger_ref,
            now,
            {"dropped": reason[:200], "score": plan.score, "stage": pair.stage},
        )
