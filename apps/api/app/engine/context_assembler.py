"""Context Assembler（文脈組立器。仕様 §3 / ENGINE_BRIEF §2.4）。

DM の返答を作る前に、次の材料を**並行して**集め、トークン予算（文字数で代用）内に収めて1つにまとめる。
  - 短期の会話履歴（messages。Gate #1 で差し止めた発言の本文はプレースホルダに置き換える）
  - 記憶（MemoryService: 質問文の埋め込み → 関連する記憶・キャラ側の記憶・期日の近い約束）
  - 世界の時間とキャラの状態（CalendarService）
  - ふたりの関係の指針（AffinityService）。呼び方の `{name}` は記憶からユーザーの名前・呼び名が分かれば置き換える
各モジュールは自分で接続を取る（並行に DB を使う）。全体の締め切りは ENGINE_CONTEXT_TIMEOUT_SECONDS。

**失敗に強くする**: モジュールが例外を出す・締め切りに間に合わない場合は、そのセクションを省いて返答を続ける
（ログと audit `engine.context_degraded` に残す）。チャット自体を失敗させない。

予算（ENGINE_BRIEF §2.4。静的なペルソナはプロンプトの先頭に置き、DeepSeek のプレフィックスキャッシュを効かせる）:
  persona 1,800 / world+state 300 / relationship 400 / memories 1,200（最大10件）/ character memories 500 /
  promises 300 / short-term history 4,000（新しい側を優先）。使った文字数は ContextBundle.budget_report。
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Final, TypeVar
from uuid import UUID

from app.core.db import Pool
from app.core.logging import get_logger
from app.engine.types import (
    STAGE_LABELS_JA,
    AffinityService,
    CalendarService,
    CharacterMemoryItem,
    CharacterStateSnapshot,
    ContextBundle,
    MemoryContext,
    MemoryItem,
    MemoryService,
    PromiseItem,
    RelationshipGuidance,
    WorldState,
    to_jst,
)
from app.services.audit import AuditLogger
from app.services.moderation import Moderator
from app.services.persona import Persona
from app.services.prompt import (
    fit_chat_history,
    render_character_memory,
    render_memory_item,
    render_promise,
    render_relationship,
    render_relationship_guidance,
    render_speech,
    render_state,
    render_world,
)
from app.services.types import HistoryItem

logger = get_logger("engine.context")

T = TypeVar("T")

# Gate #1（入力）で差し止めた発言を LLM に渡すときの置き換え文
MODERATED_PLACEHOLDER: Final[str] = "（不適切な発言のため省略）"

SECTION_HISTORY: Final[str] = "history"
SECTION_MEMORY: Final[str] = "memory"
SECTION_CALENDAR: Final[str] = "calendar"
SECTION_AFFINITY: Final[str] = "affinity"


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """セクションごとの上限（文字数。日本語はおおむね 1 文字 ≒ 1 トークン弱）。"""

    persona_chars: int = 1800
    world_state_chars: int = 300
    relationship_chars: int = 400
    memories_chars: int = 1200
    memories_max: int = 10
    character_memories_chars: int = 500
    promises_chars: int = 300
    history_chars: int = 4000
    # 履歴の先頭を「目印の発言」（ID のハッシュで 1/N の発言）にそろえる。上限で古い側を落とすとき、毎ターン先頭が
    # 1〜2 件ずつずれると DeepSeek のプレフィックスキャッシュが履歴に当たらない。目印にそろえると先頭が数ターン
    # 同じまま続き、履歴の部分もキャッシュに当たる（平均で N/2 件ほど短くなる）。1 以下で無効
    history_anchor_every: int = 8


DEFAULT_BUDGET: Final[ContextBudget] = ContextBudget()


@dataclass(frozen=True, slots=True)
class ModuleFlags:
    memory: bool = True
    calendar: bool = True
    affinity: bool = True


@dataclass(frozen=True, slots=True)
class AssembledContext:
    bundle: ContextBundle
    history: tuple[HistoryItem, ...]  # 予算内に収めた短期履歴（古い順）
    degraded: tuple[str, ...] = ()  # 失敗・時間切れで省いたセクション
    timings_ms: dict[str, int] = field(default_factory=dict)
    call_user_source: str | None = None  # 呼び方の出どころ（nickname / name / fallback / None）

    @property
    def memory_ids(self) -> list[UUID]:
        return [m.id for m in self.bundle.memory.memories]


# ---------------------------------------------------------------------------
# 世界の時間（カレンダーが無効・失敗したときの最小限のもの）
# ---------------------------------------------------------------------------

_WEEKDAYS: Final[str] = "月火水木金土日"
_SEASONS: Final[dict[int, tuple[str, str]]] = {
    **dict.fromkeys((3, 4, 5), ("spring", "春")),
    **dict.fromkeys((6, 7, 8), ("summer", "夏")),
    **dict.fromkeys((9, 10, 11), ("autumn", "秋")),
    **dict.fromkeys((12, 1, 2), ("winter", "冬")),
}


def time_of_day_ja(hour: int) -> str:
    if 4 <= hour < 7:
        return "早朝"
    if 7 <= hour < 11:
        return "朝"
    if 11 <= hour < 16:
        return "昼"
    if 16 <= hour < 19:
        return "夕方"
    if 19 <= hour < 24:
        return "夜"
    return "深夜"


def basic_world_state(now: datetime) -> WorldState:
    local = to_jst(now)
    season, season_ja = _SEASONS[local.month]
    return WorldState(
        now=now,
        now_jst=local,
        weekday_ja=_WEEKDAYS[local.weekday()],
        season=season,  # type: ignore[arg-type]
        season_ja=season_ja,
        time_of_day_ja=time_of_day_ja(local.hour),
        holiday_name=None,
        is_day_off=local.weekday() >= 5,
        seasonal_keys=(),
        seasonal_labels_ja=(),
    )


# ---------------------------------------------------------------------------
# 呼び方（{name}）の解決
# ---------------------------------------------------------------------------

_NICKNAME_RES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"[「『]([^「」『』]{1,12})[」』](?:って|と)呼(?:んで|ばれ|ぶ)"),
    re.compile(
        r"(?:呼び方|呼び名|あだ名|ニックネーム)(?:は|を)[「『]?([^」』、。\s!！?？]{1,12}?)[」』]?(?:がいい|が良い|にして|で|だ|です|に|$|[。、!！])"
    ),
    re.compile(r"([^\s「」『』、。はをがにでもの]{1,12})(?:って|と)呼(?:んで|ばれたい)"),
)
_NAME_RES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"名前は[「『]?([^」』、。\s!！?？「『]{1,12}?)[」』]?(?:だよ|です|だ|で|って|と|よ|$|[。、!！」])"),
    re.compile(
        r"([^\s「」『』、。はをがにでもの]{1,10})(?:と申します|って言います|っていいます|といいます|という名前|って名前)"
    ),
)
_HONORIFIC_RE: Final = re.compile(r"(?:さん|くん|君|ちゃん|様|さま|氏|殿)$")
_NAME_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "ユーザー",
        "あなた",
        "私",
        "わたし",
        "僕",
        "ぼく",
        "俺",
        "おれ",
        "自分",
        "きみ",
        "君",
        "キミ",
        "お前",
        "名前",
        "本名",
    }
)
NAME_PLACEHOLDER: Final[str] = "{name}"


def _clean_name(value: str) -> str | None:
    name = unicodedata.normalize("NFKC", value).strip().strip("「」『』\"' ")
    if not name or name in _NAME_STOPWORDS or "ユーザー" in name or len(name) > 12:
        return None
    return name


def extract_user_name(memories: Sequence[MemoryItem]) -> tuple[str | None, str | None]:
    """記憶からユーザーの呼び名（「たっくんって呼んで」）と名前（「名前はたかし」）を探す。

    (nickname, name) を返す。関係性（relationship）の記憶を優先し、新しい記憶を優先する。
    """
    ordered = sorted(
        (m for m in memories if m.kind in ("relationship", "fact")),
        key=lambda m: (m.kind != "relationship", -m.created_at.timestamp()),
    )
    nickname: str | None = None
    name: str | None = None
    for memory in ordered:
        text = unicodedata.normalize("NFKC", memory.content)
        if nickname is None:
            for pattern in _NICKNAME_RES:
                found = pattern.search(text)
                if found and (candidate := _clean_name(found.group(1))):
                    nickname = candidate
                    break
        if name is None:
            for pattern in _NAME_RES:
                found = pattern.search(text)
                if found and (candidate := _clean_name(_HONORIFIC_RE.sub("", found.group(1)))):
                    name = candidate
                    break
        if nickname is not None and name is not None:
            break
    return nickname, name


def fallback_call_user(persona: Persona, stage: str) -> str:
    engine = persona.engine
    if engine is not None:
        style = getattr(engine.stages, stage, None)
        if style is not None:
            return str(style.call_user_fallback)
    return persona.speech.second_person


def resolve_call_user(
    guidance: RelationshipGuidance, memories: Sequence[MemoryItem], persona: Persona
) -> tuple[RelationshipGuidance, str | None]:
    """guidance.call_user の `{name}` を記憶から解決する。

    - 呼び名の希望（「たっくんって呼んで」）があれば、段階の呼び方より優先してそのまま使う（ユーザーの希望）
    - 名前だけ分かれば、段階の呼び方（例: 「{name}さん」）に入れる
    - どちらも無ければペルソナの段階ごとの既定（call_user_fallback）→ 二人称
    """
    nickname, name = extract_user_name(memories)
    template = guidance.call_user
    if nickname is not None:
        return replace(guidance, call_user=nickname), "nickname"
    if NAME_PLACEHOLDER not in template:
        return guidance, None
    if name is not None:
        return replace(guidance, call_user=template.replace(NAME_PLACEHOLDER, name)), "name"
    return replace(guidance, call_user=fallback_call_user(persona, guidance.stage)), "fallback"


# ---------------------------------------------------------------------------
# 予算に収める
# ---------------------------------------------------------------------------


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(limit - 1, 0)] + "…"


def fit_world_state(
    world: WorldState, state: CharacterStateSnapshot | None, limit: int
) -> tuple[CharacterStateSnapshot | None, int]:
    """世界の時間 + キャラの状態を limit 文字以内にする（最近の予定 → このあと → 文言の切り詰めの順に削る）。"""
    world_chars = len(render_world(world))
    if state is None:
        return None, world_chars
    fitted = state
    while len(render_state(fitted)) + world_chars > limit and fitted.recent_events:
        fitted = replace(fitted, recent_events=fitted.recent_events[1:])
    if len(render_state(fitted)) + world_chars > limit and fitted.next_event:
        fitted = replace(fitted, next_event=None)
    if len(render_state(fitted)) + world_chars > limit:
        room = max(limit - world_chars - 80, 20)
        fitted = replace(
            fitted,
            activity=_truncate(fitted.activity, max(room // 2, 10)),
            location=_truncate(fitted.location, 20) if fitted.location else None,
            mood=_truncate(fitted.mood, 20) if fitted.mood else None,
            reply_style_hint=_truncate(fitted.reply_style_hint, max(room // 2, 10)),
        )
    return fitted, world_chars + len(render_state(fitted))


def fit_relationship(guidance: RelationshipGuidance, limit: int) -> tuple[RelationshipGuidance, int]:
    """ふたりの関係を limit 文字以内にする（例 → 話題 → 文言の切り詰めの順に削る。notes はできるだけ残す）。"""
    fitted = guidance
    while len(render_relationship_guidance(fitted)) > limit and len(fitted.examples) > 1:
        fitted = replace(fitted, examples=fitted.examples[:-1])
    while len(render_relationship_guidance(fitted)) > limit and len(fitted.topics) > 1:
        fitted = replace(fitted, topics=fitted.topics[:-1])
    if len(render_relationship_guidance(fitted)) > limit and fitted.examples:
        fitted = replace(fitted, examples=())
    if len(render_relationship_guidance(fitted)) > limit:
        fitted = replace(
            fitted,
            tone=_truncate(fitted.tone, 60),
            affection=_truncate(fitted.affection, 60),
            notes=tuple(_truncate(n, 60) for n in fitted.notes[:3]),
        )
    return fitted, len(render_relationship_guidance(fitted))


def fit_memories(memories: Sequence[MemoryItem], limit: int, max_items: int) -> tuple[tuple[MemoryItem, ...], int]:
    """記憶を limit 文字・max_items 件以内にする（呼び方などの関係性の記憶を先に確保し、残りはモジュールの順位順）。"""
    order = sorted(range(len(memories)), key=lambda i: (memories[i].kind != "relationship", i))
    kept: set[int] = set()
    used = 0
    for index in order:
        if len(kept) >= max_items:
            break
        line = len(render_memory_item(memories[index])) + 1
        if used + line > limit:
            continue
        kept.add(index)
        used += line
    return tuple(memories[i] for i in sorted(kept)), used


def fit_lines[T](items: Sequence[T], render: Callable[[T], str], limit: int) -> tuple[tuple[T, ...], int]:
    kept: list[T] = []
    used = 0
    for item in items:
        line = len(render(item)) + 1
        if used + line > limit:
            continue
        kept.append(item)
        used += line
    return tuple(kept), used


def is_history_anchor(item: HistoryItem, every: int) -> bool:
    return every <= 1 or item.id.int % every == 0


def stabilize_history_start(
    history: Sequence[HistoryItem], fitted: Sequence[HistoryItem], *, complete: bool, anchor_every: int
) -> list[HistoryItem]:
    """予算に収めた履歴（fitted = history の末尾）の先頭を、目印の発言にそろえる（プレフィックスキャッシュ用）。

    - complete: history が会話の最初からの全件（取得の上限に達していない）。全件が予算に収まっていれば、先頭は
      会話の最初で動かないので、そのまま返す。
    - それ以外（古い側を落とした）: fitted の先頭以降で最初の目印の発言から始める（無ければ fitted のまま）。
      同じ目印が残っているあいだは先頭が変わらない（新しい発言が末尾に足されるだけ）。
    """
    if anchor_every <= 1 or not fitted:
        return list(fitted)
    start = len(history) - len(fitted)
    if start == 0 and complete:
        return list(fitted)
    for offset, item in enumerate(fitted):
        if is_history_anchor(item, anchor_every):
            return list(fitted[offset:])
    return list(fitted)


def sanitize_history(items: Sequence[HistoryItem], moderator: Moderator) -> list[HistoryItem]:
    """Gate #1（入力）で差し止めたユーザー発言の本文を、LLM に渡す履歴から取り除く（直後の定型返答は残す）。"""
    return [
        replace(item, body=MODERATED_PLACEHOLDER)
        if item.sender_type == "user" and moderator.check(item.body).flagged
        else item
        for item in items
    ]


def persona_static_chars(persona: Persona) -> int:
    return len(persona.profile) + len(render_speech(persona)) + len(render_relationship(persona))


# ---------------------------------------------------------------------------
# ContextAssembler
# ---------------------------------------------------------------------------

_HISTORY_SQL: Final[str] = """
select id, sender_type, body, created_at from (
  select id, sender_type, body, created_at
    from public.messages
   where conversation_id = $1
   order by created_at desc
   limit $2
) recent
order by created_at asc
"""


class ContextAssembler:
    def __init__(
        self,
        *,
        pool: Pool,
        moderator: Moderator,
        audit: AuditLogger,
        memory: MemoryService | None,
        calendar: CalendarService | None,
        affinity: AffinityService | None,
        flags: ModuleFlags = ModuleFlags(),  # noqa: B008 - 不変の値オブジェクト
        budget: ContextBudget = DEFAULT_BUDGET,
        timeout_seconds: float = 1.5,
        history_limit: int = 60,
    ) -> None:
        self._pool = pool
        self._moderator = moderator
        self._audit = audit
        self._memory = memory if flags.memory else None
        self._calendar = calendar if flags.calendar else None
        self._affinity = affinity if flags.affinity else None
        self._budget = budget
        self._timeout = timeout_seconds
        self._history_limit = history_limit

    @property
    def budget(self) -> ContextBudget:
        return self._budget

    async def assemble(
        self,
        *,
        user_id: UUID,
        character_id: UUID,
        conversation_id: UUID,
        persona: Persona,
        user_message: str,
        now: datetime,
    ) -> AssembledContext:
        timings: dict[str, int] = {}
        started = time.perf_counter()

        async def timed(section: str, work: Awaitable[T]) -> T:
            begun = time.perf_counter()
            try:
                return await work
            finally:
                timings[section] = int((time.perf_counter() - begun) * 1000)

        tasks: dict[str, asyncio.Task[Any]] = {
            SECTION_HISTORY: asyncio.create_task(timed(SECTION_HISTORY, self._history(conversation_id))),
        }
        if self._memory is not None:
            tasks[SECTION_MEMORY] = asyncio.create_task(
                timed(
                    SECTION_MEMORY,
                    self._memory_context(
                        self._memory,
                        user_id=user_id,
                        character_id=character_id,
                        conversation_id=conversation_id,
                        user_message=user_message,
                        now=now,
                    ),
                )
            )
        if self._calendar is not None:
            tasks[SECTION_CALENDAR] = asyncio.create_task(
                timed(SECTION_CALENDAR, self._calendar_context(self._calendar, character_id, now))
            )
        if self._affinity is not None:
            tasks[SECTION_AFFINITY] = asyncio.create_task(
                timed(SECTION_AFFINITY, self._affinity.guidance(user_id=user_id, character_id=character_id, now=now))
            )
        try:
            done, pending = await asyncio.wait(tasks.values(), timeout=self._timeout)
        except BaseException:
            for task in tasks.values():
                task.cancel()
            raise
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

        errors: dict[str, str] = {}
        results: dict[str, Any] = {}
        for section, task in tasks.items():
            if task in pending:
                errors[section] = f"timeout ({self._timeout:g}s)"
                continue
            exc = task.exception() if task in done else None
            if exc is not None:
                errors[section] = f"{type(exc).__name__}: {exc}"[:500]
                continue
            results[section] = task.result()

        history: list[HistoryItem] = results.get(SECTION_HISTORY, [])
        memory_context: MemoryContext = results.get(SECTION_MEMORY) or MemoryContext((), (), ())
        world, state = results.get(SECTION_CALENDAR) or (basic_world_state(now), None)
        guidance: RelationshipGuidance | None = results.get(SECTION_AFFINITY)

        # ---- 呼び方の解決と予算
        call_user_source: str | None = None
        if guidance is not None:
            guidance, call_user_source = resolve_call_user(guidance, memory_context.memories, persona)
        budget = self._budget
        state, world_state_chars = fit_world_state(world, state, budget.world_state_chars)
        relationship_chars = 0
        if guidance is not None:
            guidance, relationship_chars = fit_relationship(guidance, budget.relationship_chars)
        memories, memories_chars = fit_memories(memory_context.memories, budget.memories_chars, budget.memories_max)
        character_memories, character_chars = fit_lines(
            memory_context.character_memories, render_character_memory, budget.character_memories_chars
        )
        promises, promises_chars = fit_lines(
            memory_context.promises, lambda p: render_promise(p, now), budget.promises_chars
        )
        fitted_history = fit_chat_history(history, max_chars=budget.history_chars)
        fitted_count = len(fitted_history)
        fitted_history = stabilize_history_start(
            history,
            fitted_history,
            complete=len(history) < self._history_limit,
            anchor_every=budget.history_anchor_every,
        )
        history_chars = sum(len(h.body) for h in fitted_history)
        persona_chars = persona_static_chars(persona)

        report = {
            "persona": persona_chars,
            "world_state": world_state_chars,
            "relationship": relationship_chars,
            "memories": memories_chars,
            "memories_count": len(memories),
            "memories_dropped": len(memory_context.memories) - len(memories),
            "character_memories": character_chars,
            "character_memories_count": len(character_memories),
            "promises": promises_chars,
            "promises_count": len(promises),
            "history": history_chars,
            "history_messages": len(fitted_history),
            "history_dropped": len(history) - len(fitted_history),
            "history_anchor_dropped": fitted_count - len(fitted_history),
            "user_message": len(user_message),
            "retrieval_skipped": int(memory_context.retrieval_skipped),
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }
        report["total"] = (
            persona_chars
            + world_state_chars
            + relationship_chars
            + memories_chars
            + character_chars
            + promises_chars
            + history_chars
            + len(user_message)
        )
        if persona_chars > budget.persona_chars:
            report["persona_over_budget"] = persona_chars - budget.persona_chars
        bundle = ContextBundle(
            world=world,
            state=state,
            relationship=guidance,
            memory=MemoryContext(
                memories=memories,
                character_memories=character_memories,
                promises=promises,
                retrieval_skipped=memory_context.retrieval_skipped,
            ),
            budget_report=report,
        )
        if errors:
            await self._report_degraded(
                errors, user_id=user_id, character_id=character_id, conversation_id=conversation_id, now=now
            )
        return AssembledContext(
            bundle=bundle,
            history=tuple(fitted_history),
            degraded=tuple(sorted(errors)),
            timings_ms=timings,
            call_user_source=call_user_source,
        )

    # ------------------------------------------------------------------ 各セクション
    async def _history(self, conversation_id: UUID) -> list[HistoryItem]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_HISTORY_SQL, conversation_id, self._history_limit)
        items = [
            HistoryItem(id=r["id"], sender_type=r["sender_type"], body=r["body"], created_at=r["created_at"])
            for r in rows
        ]
        return sanitize_history(items, self._moderator)

    @staticmethod
    async def _memory_context(
        memory: MemoryService,
        *,
        user_id: UUID,
        character_id: UUID,
        conversation_id: UUID,
        user_message: str,
        now: datetime,
    ) -> MemoryContext:
        embedding = await memory.embed_query(
            user_message, user_id=user_id, character_id=character_id, conversation_id=conversation_id
        )
        return await memory.retrieve_context(
            user_id=user_id,
            character_id=character_id,
            query_text=user_message,
            query_embedding=embedding,
            now=now,
        )

    @staticmethod
    async def _calendar_context(
        calendar: CalendarService, character_id: UUID, now: datetime
    ) -> tuple[WorldState, CharacterStateSnapshot]:
        world = calendar.world_state(now)
        state = await calendar.current_state(character_id=character_id, now=now)
        return world, state

    async def _report_degraded(
        self,
        errors: dict[str, str],
        *,
        user_id: UUID,
        character_id: UUID,
        conversation_id: UUID,
        now: datetime,
    ) -> None:
        logger.error(
            "context assembly degraded; continuing without some sections",
            extra={"fields": {"conversation_id": str(conversation_id), "errors": errors}},
        )
        await self._audit.log(
            "engine.context_degraded",
            user_id=user_id,
            character_id=character_id,
            payload={"conversation_id": conversation_id, "sections": sorted(errors), "errors": errors},
            at=now,
        )


def stage_label(stage: str) -> str:
    return STAGE_LABELS_JA.get(stage, stage)


__all__ = [
    "DEFAULT_BUDGET",
    "AssembledContext",
    "CharacterMemoryItem",
    "ContextAssembler",
    "ContextBudget",
    "ModuleFlags",
    "PromiseItem",
    "basic_world_state",
    "extract_user_name",
    "resolve_call_user",
    "sanitize_history",
    "stabilize_history_start",
]
