"""Calendar Engine（仕様 §5）の facade。`app.engine.types.CalendarService` の実装。

コア（Context Assembler・スケジューラ・post_turn ジョブ）から呼ばれる:
- `world_state(now)`: 世界の時計（C1）。DB を使わない。
- `current_state(character_id=, now=)`: 今の状態（C5）。DB の予定 + 未生成の日は生成器の結果をその場で使う。
- `ensure_schedules(now=, days_ahead=)`: 有効な全キャラの予定を「昨日〜 days_ahead 日先」まで生成（冪等）。
- `tick(now=)`: 状態の更新（character_states）・過ぎた予定の完了・キャラ側の記憶（C9）・フィード投稿（C7）。
- `sync_promise_events(...)`: 約束をカレンダーに登録（C8）。
- `check_consistency(...)`: 一貫性チェック（C11。テスト・評価ハーネス用）。

時刻はすべて呼び出し側の `now`（Clock）を使い、DB に書く日時（created_at・updated_at・published_at・監査ログ）も
`now` から明示的に渡す（評価ハーネスの早送りのため）。予定の生成は LLM を呼ばない（E7）。LLM を呼ぶのは
フィード投稿のキャプション（1 投稿 1 回。1 キャラ 1 日 最大 2 回）だけ。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from uuid import UUID

import asyncpg

from app.core.db import Connection, Pool, vector_literal
from app.core.logging import get_logger
from app.engine.calendar.captions import (
    CAPTION_PURPOSE,
    caption_messages,
    caption_mock_context,
    clean_caption,
    load_caption_template,
)
from app.engine.calendar.consistency import (
    CharacterMemoryRow,
    ConsistencyReport,
    ConsistencyViolation,
    StateRow,
    check_character,
)
from app.engine.calendar.fallback import DEFAULT_ACTIVITY
from app.engine.calendar.generator import (
    PlanContext,
    PlannedEvent,
    clip_against_existing,
    jst_day_bounds,
    plan_day,
    seeded_random,
)
from app.engine.calendar.life import LifeSpec, life_spec_from_persona
from app.engine.calendar.models import EVENT_COLUMNS, EventView
from app.engine.calendar.state import build_snapshot
from app.engine.calendar.world import WEEKDAYS_JA, build_world_state
from app.engine.types import CalendarService, CharacterStateSnapshot, OutputGuard, WorldState, jst_date, to_jst
from app.services.audit import AuditLogger
from app.services.characters import CHARACTER_COLUMNS, character_from_row
from app.services.embedding import EmbeddingClient, EmbeddingError
from app.services.llm import LLMClient, LLMError, LLMRequest
from app.services.moderation import Moderator
from app.services.persona import Persona, PersonaRepository
from app.services.prompt import PromptTemplate
from app.services.types import CharacterRecord

logger = get_logger("calendar")

# 状態の計算に使う予定の範囲（今の前後）
STATE_PAST: Final[timedelta] = timedelta(hours=48)
STATE_FUTURE: Final[timedelta] = timedelta(hours=24)

_CHARACTERS_SQL: Final[str] = f"""
select {CHARACTER_COLUMNS} from public.characters
 where is_active and ($1::uuid[] is null or id = any($1::uuid[]))
 order by id
"""  # noqa: S608 - 列名は定数

_CHARACTER_SQL: Final[str] = f"select {CHARACTER_COLUMNS} from public.characters where id = $1"  # noqa: S608

_GEN_LOCK_SQL: Final[str] = "select pg_advisory_xact_lock(hashtextextended('calendar-gen:' || $1::uuid::text, 0))"
_POST_LOCK_SQL: Final[str] = "select pg_advisory_xact_lock(hashtextextended('calendar-post:' || $1::uuid::text, 0))"
_TICK_TRY_LOCK_SQL: Final[str] = (
    "select pg_try_advisory_xact_lock(hashtextextended('calendar-tick:' || $1::uuid::text, 0))"
)

_GENERATED_DAYS_SQL: Final[str] = """
select distinct generated_for from public.character_events
 where character_id = $1 and generated_for between $2 and $3 and source in ('generator', 'seasonal')
"""

_EVENTS_WINDOW_SQL: Final[str] = f"""
select {EVENT_COLUMNS} from public.character_events
 where character_id = $1 and visibility = 'public' and status <> 'cancelled'
   and ends_at > $2 and starts_at < $3
 order by starts_at
"""  # noqa: S608 - 列名は定数

_EVENTS_BETWEEN_SQL: Final[str] = f"""
select {EVENT_COLUMNS} from public.character_events
 where character_id = $1 and ends_at > $2 and starts_at < $3
   and ($4::boolean or visibility = 'public')
 order by starts_at
"""  # noqa: S608 - 列名は定数

_INSERT_EVENT_SQL: Final[str] = """
insert into public.character_events
  (character_id, kind, title, description, location, starts_at, ends_at, mood, busyness, visibility, user_id,
   participants, status, source, source_key, generated_for, meta, created_at)
values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, 'scheduled', $13, $14, $15, $16, $17)
returning id
"""

_STATE_SQL: Final[str] = """
select character_id, activity, location, mood, busyness, event_id, status_label, updated_at
  from public.character_states where character_id = $1
"""

_UPSERT_STATE_SQL: Final[str] = """
insert into public.character_states
  (character_id, activity, location, mood, busyness, event_id, status_label, updated_at)
values ($1, $2, $3, $4, $5, $6, $7, $8)
on conflict (character_id) do update
   set activity = excluded.activity, location = excluded.location, mood = excluded.mood,
       busyness = excluded.busyness, event_id = excluded.event_id, status_label = excluded.status_label,
       updated_at = excluded.updated_at
"""

_MARK_DONE_SQL: Final[str] = f"""
update public.character_events set status = 'done'
 where character_id = $1 and status = 'scheduled' and ends_at <= $2
returning {EVENT_COLUMNS}
"""  # noqa: S608 - 列名は定数

_SET_POST_STATE_SQL: Final[str] = """
update public.character_events set meta = meta || jsonb_build_object('post_state', $2::text)
 where id = any($1::uuid[])
"""

_CLAIM_POST_SQL: Final[str] = """
update public.character_events set meta = meta || jsonb_build_object('post_state', 'generating')
 where id = $1 and post_id is null and meta->>'post_state' = 'pending'
returning id
"""

_PENDING_POSTS_SQL: Final[str] = f"""
select {EVENT_COLUMNS} from public.character_events
 where character_id = $1 and status = 'done' and post_id is null and meta->>'post_state' = 'pending'
 order by ends_at
"""  # noqa: S608 - 列名は定数

_INSERT_MEMORY_SQL: Final[str] = """
insert into public.character_memories (character_id, user_id, kind, content, occurred_at, source_event_id, created_at)
select $1, null, 'event', $2, $3, $4, $5
 where not exists (
   select 1 from public.character_memories where source_event_id = $4 and user_id is null
 )
returning id
"""

_SET_MEMORY_EMBEDDING_SQL: Final[str] = (
    "update public.character_memories set embedding = $2::text::extensions.vector where id = $1"
)

_POSTS_IN_DAY_SQL: Final[str] = """
select count(*) from public.posts where character_id = $1 and published_at >= $2 and published_at < $3
"""

_PICK_IMAGE_SQL: Final[str] = """
select image_url from public.post_image_pool i
 where (i.character_id = $1 or i.character_id is null)
   and (cardinality($2::text[]) = 0 or i.tags && $2::text[])
 order by (i.image_url in (select p.image_url from public.posts p
                            where p.character_id = $1 and p.published_at > $3)) asc,
          (i.character_id is not null) desc,
          cardinality(array(select unnest(i.tags) intersect select unnest($2::text[]))) desc,
          md5(i.id::text || $4)
 limit 1
"""

_INSERT_POST_SQL: Final[str] = """
insert into public.posts (character_id, image_url, caption, is_paid, price_tokens, published_at, created_at,
                          source_event_id)
values ($1, $2, $3, false, 0, $4, $5, $6)
returning id
"""

_LINK_POST_SQL: Final[str] = """
update public.character_events
   set post_id = $2, meta = meta || jsonb_build_object('post_state', 'posted')
 where id = $1 and post_id is null
returning id
"""

_PROMISES_SQL: Final[str] = """
select id, user_id, character_id, content, due_at, due_precision, status, event_id
  from public.promises
 where id = any($1::uuid[]) and user_id = $2 and character_id = $3
 order by created_at
 for update
"""

_EVENT_BY_ID_SQL: Final[str] = f"select {EVENT_COLUMNS} from public.character_events where id = $1"  # noqa: S608

_MEMORIES_FOR_CHECK_SQL: Final[str] = """
select id, content, occurred_at, source_event_id, created_at, user_id
  from public.character_memories
 where character_id = $1 and source_event_id is not null and occurred_at >= $2 and occurred_at < $3
"""


@dataclass(frozen=True, slots=True)
class CalendarConfig:
    """Calendar Engine の設定（既定値は ENGINE_BRIEF §2.7）。"""

    # ensure_schedules が生成する過去の日数（昨日の分。起動直後でも深夜の睡眠・直近の出来事が状態に出るように）
    past_days: int = 1
    # 予定が終わってからこの時間内なら投稿する（停止からの復帰で過去の投稿をまとめて作らない）
    post_lookback: timedelta = timedelta(hours=6)
    max_posts_per_day: int = 2  # 1 キャラ 1 日（JST・published_at 基準）あたりの投稿数の上限
    post_delay_minutes: tuple[int, int] = (10, 60)  # 予定の終了から投稿までの遅れ（決定的な乱数）
    image_reuse_days: int = 30  # この日数内に使った画像は、他に候補があれば使わない
    caption_max_chars: int = 120
    caption_max_tokens: int = 200
    caption_temperature: float = 0.9
    caption_model: str | None = None  # None = LLM_MODEL（コアが settings.llm_model_for("feed_caption") を渡す）
    prompts_dir: Path | None = None  # None = リポジトリの packages/prompts/templates
    memory_embeddings: bool = True  # キャラ側の記憶（予定由来）に埋め込みを付ける（記憶の検索用）


@dataclass(slots=True)
class TickReport:
    characters: int = 0
    state_changes: int = 0
    events_done: int = 0
    memories_created: int = 0
    posts_created: int = 0
    posts_skipped: dict[str, int] = field(default_factory=dict)
    errors: int = 0

    def skip(self, reason: str) -> None:
        self.posts_skipped[reason] = self.posts_skipped.get(reason, 0) + 1


class _AlreadyPostedError(Exception):
    """予定にはすでに投稿がある（同時に別のプロセスが投稿した）。トランザクションを取り消すために使う。"""


class _DailyCapError(Exception):
    """キャプションの生成中に、同じ日の投稿が上限に達した（同時に動いた別の tick）。"""


@dataclass(frozen=True, slots=True)
class _Caption:
    text: str
    model: str
    latency_ms: int
    usage: dict[str, int] | None


def _format_day(value: datetime) -> str:
    local = to_jst(value)
    return f"{local.month}月{local.day}日（{WEEKDAYS_JA[local.weekday()]}）"


def event_memory_content(event: EventView, *, posted: bool = False) -> str:
    """過ぎた予定をキャラ側の記憶（C9）にする文。例: 9月25日（金）19:00〜23:00、「同期と飲み会」（新宿の居酒屋）。"""
    start, end = to_jst(event.starts_at), to_jst(event.ends_at)
    end_text = f"{end:%H:%M}" if end.date() == start.date() else f"翌{end:%H:%M}"
    text = f"{_format_day(event.starts_at)}{start:%H:%M}〜{end_text}、「{event.title}」"
    if event.location:
        text += f"（{event.location}）"
    text += "。"
    if event.description:
        text += f"{event.description}。"
    if event.mood:
        text += f"気分: {event.mood}。"
    if posted:
        text += "その写真をSNSに投稿した。"
    return text[:1000]


def promise_interval(due_at: datetime | None, precision: str) -> tuple[datetime, datetime] | None:
    """約束の予定の時間帯（C8）。日時が分かれば 1 時間、日付だけなら JST のその日 1 日。それ以外は登録しない。"""
    if due_at is None:
        return None
    if precision == "datetime":
        return due_at, due_at + timedelta(hours=1)
    if precision == "day":
        return jst_day_bounds(jst_date(due_at))
    return None


class CalendarEngine:
    """キャラクターカレンダー（仕様 §5）。コアが app/container.py で組み立てる。"""

    def __init__(
        self,
        *,
        pool: Pool,
        llm: LLMClient,
        audit: AuditLogger,
        personas: PersonaRepository,
        moderator: Moderator | None = None,
        output_guard: OutputGuard | None = None,
        embedder: EmbeddingClient | None = None,
        config: CalendarConfig | None = None,
        caption_template: PromptTemplate | None = None,
    ) -> None:
        self._pool = pool
        self._llm = llm
        self._audit = audit
        self._personas = personas
        self._moderator = moderator or Moderator()
        self._guard = output_guard
        self._embedder = embedder
        self._config = config or CalendarConfig()
        self._caption_template = caption_template or load_caption_template(self._config.prompts_dir)
        self._spec_cache: dict[tuple[object, ...], LifeSpec] = {}

    @property
    def config(self) -> CalendarConfig:
        return self._config

    # ------------------------------------------------------------------ 世界の時計（C1）
    def world_state(self, now: datetime) -> WorldState:
        return build_world_state(now)

    # ------------------------------------------------------------------ ペルソナ
    def persona_and_spec(self, character: CharacterRecord) -> tuple[Persona, LifeSpec]:
        persona = self._personas.for_character(character)
        key: tuple[object, ...] = (
            ("fallback", persona.key, persona.name, persona.schedule_pattern)
            if persona.is_fallback or persona.engine is None
            else ("engine", persona.key, id(persona))
        )
        spec = self._spec_cache.get(key)
        if spec is None:
            spec = life_spec_from_persona(persona)
            self._spec_cache[key] = spec
        return persona, spec

    async def _fetch_characters(self, conn: Connection, character_ids: Sequence[UUID] | None) -> list[CharacterRecord]:
        rows = await conn.fetch(_CHARACTERS_SQL, list(character_ids) if character_ids is not None else None)
        return [character_from_row(r) for r in rows]

    def _plan_context(self, spec: LifeSpec, character_id: UUID, characters: Sequence[CharacterRecord]) -> PlanContext:
        if not spec.friend_names:
            return PlanContext()
        companions = {c.name: c.id for c in characters if c.id != character_id and c.name in spec.friend_names}
        return PlanContext(companions=companions)

    # ------------------------------------------------------------------ 生成（C2〜C4）
    async def ensure_schedules(
        self, *, now: datetime, days_ahead: int, character_ids: Sequence[UUID] | None = None
    ) -> int:
        """有効なキャラ全員（または character_ids）の予定を、昨日〜 days_ahead 日先まで生成する。作った件数を返す。

        (キャラ, JST の日付) ごとに冪等（generated_for で生成済みの日は作らない）。LLM は呼ばない。
        """
        today = jst_date(now)
        first = today - timedelta(days=max(self._config.past_days, 0))
        last = today + timedelta(days=max(days_ahead, 1) - 1)
        async with self._pool.acquire() as conn:
            characters = await self._fetch_characters(conn, character_ids)
        total = 0
        for character in characters:
            try:
                total += await self._ensure_character(character, characters, first, last, now)
            except (asyncpg.PostgresError, OSError, TimeoutError):
                logger.exception("calendar generation failed", extra={"fields": {"character_id": str(character.id)}})
        return total

    async def _ensure_character(
        self,
        character: CharacterRecord,
        characters: Sequence[CharacterRecord],
        first: date,
        last: date,
        now: datetime,
    ) -> int:
        persona, spec = self.persona_and_spec(character)
        context = self._plan_context(spec, character.id, characters)
        created: list[tuple[UUID, PlannedEvent]] = []
        dropped: list[PlannedEvent] = []
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(_GEN_LOCK_SQL, character.id)
            generated = {r["generated_for"] for r in await conn.fetch(_GENERATED_DAYS_SQL, character.id, first, last)}
            missing: list[date] = []
            day = first
            while day <= last:
                if day not in generated:
                    missing.append(day)
                day += timedelta(days=1)
            for day in missing:
                planned = plan_day(spec, character.id, day, context)
                if not planned:
                    continue
                window_start = min(e.starts_at for e in planned)
                window_end = max(e.ends_at for e in planned)
                existing_rows = await conn.fetch(_EVENTS_WINDOW_SQL, character.id, window_start, window_end)
                existing = [(r["starts_at"], r["ends_at"]) for r in existing_rows]
                kept, clipped_out = clip_against_existing(planned, existing)
                dropped.extend(clipped_out)
                for event in kept:
                    try:
                        async with conn.transaction():
                            event_id = await conn.fetchval(
                                _INSERT_EVENT_SQL,
                                character.id,
                                event.kind,
                                event.title[:200],
                                event.description[:1000] if event.description else None,
                                event.location[:200] if event.location else None,
                                event.starts_at,
                                event.ends_at,
                                event.mood[:100] if event.mood else None,
                                event.busyness,
                                "public",
                                None,
                                list(event.participants),
                                event.source,
                                event.source_key,
                                event.generated_for,
                                event.meta(),
                                now,
                            )
                    except asyncpg.ExclusionViolationError:
                        # 同時に別の経路で予定が入った（排他制約 character_events_no_overlap）。この予定は入れない
                        dropped.append(event)
                        continue
                    created.append((event_id, event))
        if created:
            # 予定が 1 件も無い日（ルーティンの無い曜日など）は生成済みの印が残らず毎回ここに来るが、何も作らないので
            # 監査ログも残さない（状態の変化が無い）
            await self._audit.log(
                "calendar.generate",
                character_id=character.id,
                payload={
                    "persona_key": persona.key,
                    "spec_source": spec.source,
                    "dates": sorted({e.generated_for.isoformat() for _, e in created}),
                    "event_count": len(created),
                    "events": [
                        {
                            "id": event_id,
                            "kind": e.kind,
                            "source_key": e.source_key,
                            "title": e.title,
                            "starts_at": e.starts_at,
                            "ends_at": e.ends_at,
                        }
                        for event_id, e in created
                    ],
                    "llm_calls": 0,
                },
                at=now,
            )
        if dropped:
            await self._audit.log(
                "calendar.conflict",
                character_id=character.id,
                payload={
                    "reason": "overlaps_existing_event",
                    "dropped": [
                        {"kind": e.kind, "title": e.title, "starts_at": e.starts_at, "ends_at": e.ends_at}
                        for e in dropped
                    ],
                },
                at=now,
            )
        return len(created)

    # ------------------------------------------------------------------ 状態（C5）
    async def _events_view(
        self, conn: Connection, character: CharacterRecord, spec: LifeSpec, now: datetime
    ) -> list[EventView]:
        """now の前後の公開の予定。まだ生成されていない日は、生成器の結果をその場で使う（DB には書かない）。"""
        rows = await conn.fetch(_EVENTS_WINDOW_SQL, character.id, now - STATE_PAST, now + STATE_FUTURE)
        views = [EventView.from_row(r) for r in rows]
        first = jst_date(now - STATE_PAST)
        last = jst_date(now + STATE_FUTURE)
        generated = {r["generated_for"] for r in await conn.fetch(_GENERATED_DAYS_SQL, character.id, first, last)}
        planned: list[PlannedEvent] = []
        day = first
        while day <= last:
            if day not in generated:
                planned.extend(plan_day(spec, character.id, day))
            day += timedelta(days=1)
        if planned:
            kept, _ = clip_against_existing(planned, [(v.starts_at, v.ends_at) for v in views])
            views.extend(EventView.from_planned(e) for e in kept)
            views.sort(key=lambda v: v.starts_at)
        return views

    async def current_state(self, *, character_id: UUID, now: datetime) -> CharacterStateSnapshot:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_CHARACTER_SQL, character_id)
            if row is None:
                # キャラが見つからない（Context Assembler は通常キャラを確認済み。念のため）
                return build_snapshot([], DEFAULT_ACTIVITY, now)
            character = character_from_row(row)
            _persona, spec = self.persona_and_spec(character)
            views = await self._events_view(conn, character, spec, now)
        return build_snapshot(views, spec.default, now)

    async def events_between(
        self, *, character_id: UUID, start: datetime, end: datetime, include_user_events: bool = False
    ) -> list[EventView]:
        """start〜end と重なる予定（評価ハーネス・テスト・デバッグ用）。"""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_EVENTS_BETWEEN_SQL, character_id, start, end, include_user_events)
        return [EventView.from_row(r) for r in rows]

    # ------------------------------------------------------------------ tick（C5・C7・C9）
    async def tick(self, *, now: datetime) -> None:
        await self.run_tick(now=now)

    async def run_tick(self, *, now: datetime, character_ids: Sequence[UUID] | None = None) -> TickReport:
        """状態の更新・過ぎた予定の完了・キャラ側の記憶・フィード投稿。結果の件数を返す（テスト・評価用）。"""
        report = TickReport()
        async with self._pool.acquire() as conn:
            characters = await self._fetch_characters(conn, character_ids)
        world = build_world_state(now)
        for character in characters:
            report.characters += 1
            try:
                await self._tick_character(character, now, report)
                await self._publish_posts(character, now, world, report)
            except (asyncpg.PostgresError, OSError, TimeoutError):
                report.errors += 1
                logger.exception("calendar tick failed", extra={"fields": {"character_id": str(character.id)}})
        return report

    def _should_post(self, character_id: UUID, event: EventView, now: datetime) -> bool:
        probability = event.post_probability
        if probability <= 0 or not event.is_public or event.ends_at <= now - self._config.post_lookback:
            return False
        rng = seeded_random(character_id, "post", event.source_key, event.generated_for, event.meta.get("segment"))
        return rng.random() < probability

    async def _tick_character(self, character: CharacterRecord, now: datetime, report: TickReport) -> None:
        _persona, spec = self.persona_and_spec(character)
        memories: list[tuple[UUID, EventView, str]] = []
        state_change: dict[str, Any] | None = None
        async with self._pool.acquire() as conn, conn.transaction():
            if not await conn.fetchval(_TICK_TRY_LOCK_SQL, character.id):
                return  # 別のプロセスが同じキャラを処理中
            views = await self._events_view(conn, character, spec, now)
            snapshot = build_snapshot(views, spec.default, now)
            previous = await conn.fetchrow(_STATE_SQL, character.id)
            new_state = {
                "activity": snapshot.activity[:200],
                "location": snapshot.location,
                "mood": snapshot.mood,
                "busyness": snapshot.busyness,
                "event_id": snapshot.event_id,
                "status_label": (snapshot.status_label or "")[:40] or None,
            }
            old_state = {k: previous[k] for k in new_state} if previous is not None else None
            if old_state != new_state:
                await conn.execute(
                    _UPSERT_STATE_SQL,
                    character.id,
                    new_state["activity"],
                    new_state["location"],
                    new_state["mood"],
                    new_state["busyness"],
                    new_state["event_id"],
                    new_state["status_label"],
                    now,
                )
                if old_state is None or (old_state["event_id"], old_state["activity"]) != (
                    new_state["event_id"],
                    new_state["activity"],
                ):
                    state_change = {"from": old_state, "to": new_state, "event_kind": snapshot.event_kind}
            done_rows = await conn.fetch(_MARK_DONE_SQL, character.id, now)
            done = [EventView.from_row(r) for r in done_rows]
            post_ids: list[UUID] = []
            for event in done:
                if not event.is_public or event.id is None:
                    continue
                if event.kind in ("oneoff", "seasonal") and event.notable:
                    content = event_memory_content(event)
                    memory_id = await conn.fetchval(
                        _INSERT_MEMORY_SQL, character.id, content, event.starts_at, event.id, now
                    )
                    if memory_id is not None:
                        memories.append((memory_id, event, content))
                if self._should_post(character.id, event, now):
                    post_ids.append(event.id)
            if post_ids:
                await conn.execute(_SET_POST_STATE_SQL, post_ids, "pending")
        if state_change is not None:
            report.state_changes += 1
            await self._audit.log("calendar.state_change", character_id=character.id, payload=state_change, at=now)
        if done:
            report.events_done += len(done)
            await self._audit.log(
                "calendar.event_done",
                character_id=character.id,
                payload={
                    "events": [
                        {
                            "id": e.id,
                            "kind": e.kind,
                            # 約束の予定（ユーザーだけの予定）の内容はユーザーの発言由来なので、ここには載せない
                            "title": e.title if e.is_public else None,
                            "ends_at": e.ends_at,
                            "visibility": e.visibility,
                        }
                        for e in done
                    ],
                    "post_candidates": post_ids,
                },
                at=now,
            )
        await self._record_memories(character.id, memories, now, report)

    async def _record_memories(
        self, character_id: UUID, memories: Sequence[tuple[UUID, EventView, str]], now: datetime, report: TickReport
    ) -> None:
        for memory_id, event, content in memories:
            report.memories_created += 1
            await self._audit.log(
                "character_memory.create",
                character_id=character_id,
                payload={
                    "character_memory_id": memory_id,
                    "kind": "event",
                    "shared": True,
                    "source_event_id": event.id,
                    "content": content,
                    "origin": "calendar",
                },
                at=now,
            )
        if not memories or self._embedder is None or not self._config.memory_embeddings:
            return
        try:
            vectors = await self._embedder.embed([content for _, _, content in memories])
        except (EmbeddingError, OSError, TimeoutError) as exc:
            logger.warning(
                "character memory embedding failed; stored without embedding",
                extra={"fields": {"character_id": str(character_id), "error": repr(exc)}},
            )
            await self._audit.log(
                "llm.error",
                character_id=character_id,
                payload={
                    "purpose": "character_memory_embedding",
                    "error": str(exc),
                    "character_memory_ids": [m for m, _, _ in memories],
                },
                at=now,
            )
            return
        async with self._pool.acquire() as conn:
            for (memory_id, _event, _content), vector in zip(memories, vectors, strict=False):
                await conn.execute(_SET_MEMORY_EMBEDDING_SQL, memory_id, vector_literal(vector))

    # ------------------------------------------------------------------ フィード投稿（C7）
    def _post_delay(self, character_id: UUID, event: EventView) -> timedelta:
        low, high = self._config.post_delay_minutes
        rng = seeded_random(character_id, "post-delay", event.id)
        return timedelta(minutes=rng.randint(min(low, high), max(low, high)))

    async def _publish_posts(
        self, character: CharacterRecord, now: datetime, world: WorldState, report: TickReport
    ) -> None:
        async with self._pool.acquire() as conn:
            pending = [EventView.from_row(r) for r in await conn.fetch(_PENDING_POSTS_SQL, character.id)]
        if not pending:
            return
        persona, _spec = self.persona_and_spec(character)
        for event in pending:
            if event.id is None:
                continue
            async with self._pool.acquire() as conn:
                if event.ends_at <= now - self._config.post_lookback:
                    await conn.execute(_SET_POST_STATE_SQL, [event.id], "expired")
                    report.skip("expired")
                    continue
                published_at = event.ends_at + self._post_delay(character.id, event)
                day_start, day_end = jst_day_bounds(jst_date(published_at))
                count = int(await conn.fetchval(_POSTS_IN_DAY_SQL, character.id, day_start, day_end) or 0)
                if count >= self._config.max_posts_per_day:
                    await conn.execute(_SET_POST_STATE_SQL, [event.id], "skipped_daily_cap")
                    report.skip("daily_cap")
                    continue
                if await conn.fetchval(_CLAIM_POST_SQL, event.id) is None:
                    continue  # 別のプロセスが処理中
            caption, reason = await self._generate_caption(character, persona, event, world, now)
            if caption is None:
                async with self._pool.acquire() as conn:
                    await conn.execute(_SET_POST_STATE_SQL, [event.id], reason)
                report.skip(reason)
                continue
            await self._insert_post(
                character, event=event, caption=caption, published_at=published_at, now=now, report=report
            )

    async def _generate_caption(
        self, character: CharacterRecord, persona: Persona, event: EventView, world: WorldState, now: datetime
    ) -> tuple[_Caption | None, str]:
        max_chars = self._config.caption_max_chars
        messages = caption_messages(self._caption_template, persona, event, world, max_chars=max_chars)
        seed = f"{character.id}|{event.source_key}|{event.generated_for}|{event.meta.get('segment')}"
        request = LLMRequest(
            purpose=CAPTION_PURPOSE,
            messages=messages,
            temperature=self._config.caption_temperature,
            max_tokens=self._config.caption_max_tokens,
            model=self._config.caption_model,
            mock_context=caption_mock_context(persona, event, seed=seed, max_chars=max_chars),
        )
        try:
            result = await self._llm.complete(request)
        except LLMError as exc:
            await self._audit.log(
                "llm.error",
                character_id=character.id,
                payload={
                    "purpose": CAPTION_PURPOSE,
                    "event_id": event.id,
                    "error": str(exc),
                    "status_code": exc.status_code,
                    "attempts": exc.attempts,
                },
                at=now,
            )
            return None, "llm_error"
        text = clean_caption(result.text, persona.name, max_chars=max_chars)
        if not text:
            await self._audit.log(
                "llm.error",
                character_id=character.id,
                payload={
                    "purpose": CAPTION_PURPOSE,
                    "event_id": event.id,
                    "error": "empty caption",
                    "model": result.model,
                    "usage": result.usage,
                },
                at=now,
            )
            return None, "empty"
        # 差し止めた場合も、コスト集計のため用途・モデル・トークン数を監査ログに残す（E7）
        base = {
            "stage": "output",
            "context": CAPTION_PURPOSE,
            "purpose": CAPTION_PURPOSE,
            "event_id": event.id,
            "text": text,
            "model": result.model,
            "usage": result.usage,
        }
        moderation = self._moderator.check(text, extra_ng_words=persona.speech.ng_words, block_links=True)
        if moderation.flagged:
            await self._audit.log(
                "moderation.flag",
                character_id=character.id,
                payload={**base, "categories": moderation.categories, "matched_terms": moderation.matched_terms},
                at=now,
            )
            return None, "moderated"
        if self._guard is not None:
            guard = self._guard.check(text)
            if guard.flagged:
                await self._audit.log(
                    "moderation.flag",
                    character_id=character.id,
                    payload={**base, "categories": list(guard.categories), "matched_terms": list(guard.matched)},
                    at=now,
                )
                return None, "guard"
        return _Caption(text=text, model=result.model, latency_ms=result.latency_ms, usage=result.usage), "ok"

    async def _pick_image(self, conn: Connection, character_id: UUID, event: EventView, now: datetime) -> str:
        since = now - timedelta(days=self._config.image_reuse_days)
        seed = str(event.id)
        tags = list(event.post_tags)
        url = await conn.fetchval(_PICK_IMAGE_SQL, character_id, tags, since, seed)
        if url is None and tags:
            url = await conn.fetchval(_PICK_IMAGE_SQL, character_id, [], since, seed)
        if url is None:
            # プールが空（シード未投入）でも投稿できるようにする（開発用のプレースホルダ画像）
            digest = hashlib.sha256(f"{character_id}|{event.id}".encode()).hexdigest()[:16]
            url = f"https://picsum.photos/seed/evt-{digest}/1080/1080"
        return str(url)

    async def _insert_post(
        self,
        character: CharacterRecord,
        *,
        event: EventView,
        caption: _Caption,
        published_at: datetime,
        now: datetime,
        report: TickReport,
    ) -> None:
        memory: tuple[UUID, EventView, str] | None = None
        day_start, day_end = jst_day_bounds(jst_date(published_at))
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                # 1 日の上限は、キャラ単位のロックの中で数え直してから入れる（同時に動いた tick でも超えない）
                await conn.execute(_POST_LOCK_SQL, character.id)
                count = int(await conn.fetchval(_POSTS_IN_DAY_SQL, character.id, day_start, day_end) or 0)
                if count >= self._config.max_posts_per_day:
                    raise _DailyCapError
                image_url = await self._pick_image(conn, character.id, event, now)
                post_id = await conn.fetchval(
                    _INSERT_POST_SQL, character.id, image_url, caption.text, published_at, now, event.id
                )
                if await conn.fetchval(_LINK_POST_SQL, event.id, post_id) is None:
                    raise _AlreadyPostedError  # 投稿の挿入ごと取り消す
                if event.kind == "routine":
                    # ルーティンは投稿したときだけ記憶に残す（毎日の「仕事」で記憶を埋めない）
                    content = event_memory_content(event, posted=True)
                    memory_id = await conn.fetchval(
                        _INSERT_MEMORY_SQL, character.id, content, event.starts_at, event.id, now
                    )
                    if memory_id is not None:
                        memory = (memory_id, event, content)
        except _AlreadyPostedError:
            report.skip("already_posted")
            return
        except _DailyCapError:
            async with self._pool.acquire() as conn:
                await conn.execute(_SET_POST_STATE_SQL, [event.id], "skipped_daily_cap")
            report.skip("daily_cap")
            return
        report.posts_created += 1
        await self._audit.log(
            "calendar.post_create",
            character_id=character.id,
            payload={
                "post_id": post_id,
                "event_id": event.id,
                "event_kind": event.kind,
                "caption": caption.text,
                "image_url": image_url,
                "tags": list(event.post_tags),
                "published_at": published_at,
                "purpose": CAPTION_PURPOSE,
                "model": caption.model,
                "latency_ms": caption.latency_ms,
                "usage": caption.usage,
            },
            at=now,
        )
        if memory is not None:
            await self._record_memories(character.id, [memory], now, report)

    # ------------------------------------------------------------------ 約束（C8）
    async def sync_promise_events(
        self, *, user_id: UUID, character_id: UUID, promise_ids: Sequence[UUID], now: datetime
    ) -> None:
        """約束を、そのユーザーだけの予定（visibility='user', kind='promise'）としてカレンダーに登録する。

        冪等。期日が変わっていれば予定を動かし、取り消された約束の予定は取り消す。期日が曖昧（週・月・不明）な
        約束は予定にしない（約束そのものは Memory Engine が扱う）。
        """
        if not promise_ids:
            return
        audits: list[dict[str, Any]] = []
        async with self._pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(_PROMISES_SQL, list(promise_ids), user_id, character_id)
            for promise in rows:
                result = await self._sync_promise(conn, promise, now)
                if result is not None:
                    audits.append(result)
        for payload in audits:
            await self._audit.log(
                "calendar.promise_event", user_id=user_id, character_id=character_id, payload=payload, at=now
            )

    async def _sync_promise(self, conn: Connection, promise: asyncpg.Record, now: datetime) -> dict[str, Any] | None:
        existing = await conn.fetchrow(_EVENT_BY_ID_SQL, promise["event_id"]) if promise["event_id"] else None
        base = {"promise_id": promise["id"], "due_precision": promise["due_precision"]}
        if promise["status"] == "cancelled":
            if existing is not None and existing["status"] == "scheduled":
                await conn.execute(
                    "update public.character_events set status = 'cancelled' where id = $1", existing["id"]
                )
                return {**base, "action": "cancelled", "event_id": existing["id"]}
            return None
        if promise["status"] == "done":
            return None
        interval = promise_interval(promise["due_at"], promise["due_precision"])
        if interval is None:
            logger.info(
                "promise has no precise due date; not added to the calendar",
                extra={"fields": {"promise_id": str(promise["id"]), "precision": promise["due_precision"]}},
            )
            return None
        start, end = interval
        title = str(promise["content"])[:200]
        if existing is not None:
            if (existing["starts_at"], existing["ends_at"], existing["title"]) == (start, end, title):
                return None
            await conn.execute(
                """
                update public.character_events set starts_at = $2, ends_at = $3, title = $4,
                       status = case when status = 'cancelled' then 'scheduled' else status end
                 where id = $1
                """,
                existing["id"],
                start,
                end,
                title,
            )
            return {**base, "action": "updated", "event_id": existing["id"], "starts_at": start, "ends_at": end}
        event_id = await conn.fetchval(
            _INSERT_EVENT_SQL,
            promise["character_id"],
            "promise",
            title,
            None,
            None,
            start,
            end,
            None,
            1,
            "user",
            promise["user_id"],
            [],
            "promise",
            f"promise:{promise['id']}",
            None,
            {"promise_id": str(promise["id"]), "due_precision": promise["due_precision"]},
            now,
        )
        await conn.execute("update public.promises set event_id = $2 where id = $1", promise["id"], event_id)
        return {**base, "action": "created", "event_id": event_id, "starts_at": start, "ends_at": end}

    # ------------------------------------------------------------------ 一貫性チェック（C11）
    async def check_consistency(
        self,
        *,
        start: date,
        end: date,
        character_ids: Sequence[UUID] | None = None,
        include_memories: bool = True,
        include_state: bool = True,
    ) -> ConsistencyReport:
        """start〜end（JST の日付、両端を含む）の予定・キャラ側の記憶・状態の一貫性を検査する（評価ハーネス用）。

        error が 0 件なら「予定の一貫性」の合格条件（同時刻の重複・性格と合わない予定 0 件）を満たす。
        """
        range_start = jst_day_bounds(start - timedelta(days=1))[0]
        range_end = jst_day_bounds(end + timedelta(days=1))[1]
        violations: list[ConsistencyViolation] = []
        per_character: dict[str, int] = {}
        async with self._pool.acquire() as conn:
            characters = await self._fetch_characters(conn, character_ids)
            for character in characters:
                _persona, spec = self.persona_and_spec(character)
                rows = await conn.fetch(_EVENTS_BETWEEN_SQL, character.id, range_start, range_end, False)
                events = [EventView.from_row(r) for r in rows]
                per_character[str(character.id)] = sum(
                    1 for e in events if start <= (e.generated_for or jst_date(e.starts_at)) <= end
                )
                memories: list[CharacterMemoryRow] = []
                if include_memories:
                    memories = [
                        CharacterMemoryRow(
                            id=r["id"],
                            content=r["content"],
                            occurred_at=r["occurred_at"],
                            source_event_id=r["source_event_id"],
                            created_at=r["created_at"],
                            user_id=r["user_id"],
                        )
                        for r in await conn.fetch(_MEMORIES_FOR_CHECK_SQL, character.id, range_start, range_end)
                    ]
                state: StateRow | None = None
                if include_state:
                    row = await conn.fetchrow(_STATE_SQL, character.id)
                    if row is not None and range_start <= row["updated_at"] < range_end:
                        state = StateRow(
                            character_id=character.id,
                            activity=row["activity"],
                            event_id=row["event_id"],
                            updated_at=row["updated_at"],
                        )
                violations.extend(
                    check_character(
                        spec,
                        events,
                        character_id=character.id,
                        first=start,
                        last=end,
                        memories=memories,
                        state=state,
                    )
                )
        return ConsistencyReport(
            characters=len(per_character),
            events=sum(per_character.values()),
            violations=tuple(violations),
            per_character=per_character,
        )


def _conforms_to_protocol(engine: CalendarEngine) -> CalendarService:
    """mypy に CalendarService（app/engine/types.py の契約）を満たすことを検査させる。"""
    return engine
