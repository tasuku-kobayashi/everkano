"""予定の生成（仕様 §5 C2〜C4・C10・C11）。決定的・ルールベース・LLM 呼び出しなし（E7）。

- 繰り返しの予定（routine）: ペルソナの `life.routine` を曜日ごとに展開する（日またぎの睡眠を含む）。
- 単発の出来事（oneoff）: `life.events` のテンプレートを、週ごとに乱数で選ぶ。乱数の種は
  hash(キャラ, ISO 週, テンプレートのキー) なので、同じキャラ・同じ週なら何度生成しても同じ結果になる。
  `weekly_probability`（その週に起こる確率）/ `days`（曜日）/ `months`（月）を守る。
  `min_interval_days`（前回からの最短間隔 M 日）があるテンプレートは、週の代わりに L = ceil((M + 7) / 7) 週の
  「期間」ごとに 1 回まで抽選し（当たる確率 = 1 - (1 - weekly_probability)^L）、日付は期間の最初の 7L - M 日の
  中から選ぶ。こうすると隣り合う期間の出来事は必ず M 日以上離れ、前の期間の結果を見ずに決まる（どの日から
  生成しても同じ結果・計算量が一定）。期間の区切りはキャラ × テンプレートごとにずらす（全員が同じ週に揃わない）。
- 行事（seasonal）: `seasonal[].attends` の行事を、その年の期間の中の 1 日に入れる（期間が 1 日なら固定。
  期間があれば、仕事のルーティンと重ならない日 → 休日 → 任意の日の順に、乱数で選ぶ）。
- 誕生日: `life.birthday` の日の夜。
- 置き換え: 行事・誕生日 > 単発 > ルーティンの優先度で置き、ルーティンは重なる部分を切り取る（分割 / 短すぎれば
  落とす）。単発どうし・行事どうしが重なったら後のものを落とす。公開の予定は同じ時刻に 2 つ存在しない
  （DB の排他制約 character_events_no_overlap でも保証する）。
- 日付 D の予定は D-1〜D+1 の候補をまとめて解決してから D の分だけを取り出す（日またぎの予定と翌日の予定の
  重なりを、どちらの日を先に生成しても同じように解決するため）。
- キャラの性格との一貫性は「ペルソナが書いたテンプレートからしか予定を作らない」ことで担保する（C11）。
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any, Final, Literal
from uuid import UUID

from app.engine.calendar.life import BIRTHDAY_TIMES, LifeSpec, OneoffSpec, SeasonalSpec, interval_on
from app.engine.calendar.world import is_day_off, seasonal_window
from app.engine.types import JST

EventKindName = Literal["routine", "oneoff", "seasonal"]
EventSource = Literal["generator", "seasonal"]

# ルーティンを切り取った残りがこれより短ければ落とす
MIN_ROUTINE_SEGMENT: Final[timedelta] = timedelta(minutes=30)
BIRTHDAY_SOURCE_KEY: Final[str] = "birthday"
BIRTHDAY_TITLE: Final[str] = "誕生日のお祝い"
BIRTHDAY_MOOD: Final[str] = "誕生日でちょっと浮かれている"
BIRTHDAY_LABEL: Final[str] = "誕生日"
BIRTHDAY_TAGS: Final[tuple[str, ...]] = ("sweets",)
BIRTHDAY_POST_PROBABILITY: Final[float] = 0.8

# 優先度（小さいほど強い）
_PRIORITY: Final[dict[str, int]] = {"seasonal": 0, "birthday": 0, "oneoff": 1, "routine": 2}


@dataclass(frozen=True, slots=True)
class PlannedEvent:
    """生成器が作る予定（DB に入れる前）。日時は timezone-aware（JST）。"""

    kind: EventKindName
    source: EventSource
    source_key: str
    title: str
    starts_at: datetime
    ends_at: datetime
    generated_for: date
    busyness: int
    status_label: str
    location: str | None = None
    description: str | None = None
    mood: str | None = None
    post_tags: tuple[str, ...] = ()
    post_probability: float = 0.0
    participants: tuple[UUID, ...] = ()
    notable: bool = False  # 過ぎたらキャラ側の記憶（C9）に残す
    segment: int | None = None  # 切り取られて分割されたルーティンの番号

    @property
    def priority(self) -> int:
        return _PRIORITY["birthday" if self.source_key == BIRTHDAY_SOURCE_KEY else self.kind]

    def overlaps(self, start: datetime, end: datetime) -> bool:
        return self.starts_at < end and start < self.ends_at

    def meta(self) -> dict[str, Any]:
        """character_events.meta に保存する生成の情報。"""
        data: dict[str, Any] = {
            "status_label": self.status_label,
            "post_tags": list(self.post_tags),
            "post_probability": self.post_probability,
            "notable": self.notable,
        }
        if self.segment is not None:
            data["segment"] = self.segment
        return data


@dataclass(frozen=True, slots=True)
class PlanContext:
    """生成の追加情報（C10: 一緒に過ごす別キャラの名前 → キャラ ID）。"""

    companions: Mapping[str, UUID] = field(default_factory=dict)


def _seed(*parts: object) -> int:
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def seeded_random(*parts: object) -> random.Random:
    return random.Random(_seed(*parts))


def iso_week_monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


# ---------------------------------------------------------------------------
# 候補
# ---------------------------------------------------------------------------


def routine_candidates(spec: LifeSpec, day: date) -> list[PlannedEvent]:
    events: list[PlannedEvent] = []
    for block in spec.routines_on(day):
        start, end = interval_on(day, block.start, block.end)
        events.append(
            PlannedEvent(
                kind="routine",
                source="generator",
                source_key=block.key,
                title=block.activity,
                starts_at=start,
                ends_at=end,
                generated_for=day,
                busyness=block.busyness,
                status_label=block.status_label,
                location=block.location,
                mood=block.mood,
                post_tags=block.post_tags,
                post_probability=block.post_probability,
            )
        )
    return events


# 期間の起点（月曜）。キャラ × テンプレートごとに 0〜(期間の日数 - 1) 日ずらす
_PERIOD_EPOCH: Final[date] = date(2000, 1, 3)


def _weekly_occurrence(spec: LifeSpec, template: OneoffSpec, character_id: UUID, monday: date) -> date | None:
    """その週にテンプレートが起こる日（min_interval_days が無いテンプレート）。"""
    iso = monday.isocalendar()
    rng = seeded_random(character_id, f"{iso.year}-W{iso.week:02d}", template.key)
    if rng.random() >= template.weekly_probability:
        return None
    allowed = [
        d
        for d in (monday + timedelta(days=i) for i in range(7))
        if spec.effective_weekday(d) in template.days and (template.months is None or d.month in template.months)
    ]
    if not allowed:
        return None
    return rng.choice(allowed)


def interval_period(template: OneoffSpec, character_id: UUID, day: date) -> tuple[date, int, int]:
    """min_interval_days のあるテンプレートの、day を含む期間（開始日, 期間の日数, 日付を選べる先頭の日数）。"""
    weeks = -(-(template.min_interval_days + 7) // 7)
    length = weeks * 7
    shift = seeded_random(character_id, "period-shift", template.key).randrange(length)
    anchor = _PERIOD_EPOCH + timedelta(days=shift)
    index = (day - anchor).days // length
    start = anchor + timedelta(days=index * length)
    return start, length, length - template.min_interval_days


def _period_occurrence(spec: LifeSpec, template: OneoffSpec, character_id: UUID, day: date) -> date | None:
    start, length, window = interval_period(template, character_id, day)
    weeks = length // 7
    rng = seeded_random(character_id, "period", start.isoformat(), template.key)
    if rng.random() >= 1 - (1 - template.weekly_probability) ** weeks:
        return None
    allowed = [
        d
        for d in (start + timedelta(days=i) for i in range(window))
        if spec.effective_weekday(d) in template.days and (template.months is None or d.month in template.months)
    ]
    if not allowed:
        return None
    return rng.choice(allowed)


def oneoff_occurs_on(spec: LifeSpec, template: OneoffSpec, character_id: UUID, day: date) -> bool:
    """テンプレートが day に起こるか（週の抽選。min_interval_days があれば期間の抽選）。"""
    if template.min_interval_days > 0:
        return _period_occurrence(spec, template, character_id, day) == day
    return _weekly_occurrence(spec, template, character_id, iso_week_monday(day)) == day


def _companions_in(text: str, context: PlanContext, spec: LifeSpec) -> tuple[UUID, ...]:
    """テンプレートの文面に出てくる友人（別キャラ）を participants にする（C10。MVP では構造のみ）。"""
    found: list[UUID] = []
    for name, cid in sorted(context.companions.items()):
        if name in spec.friend_names and name in text and cid not in found:
            found.append(cid)
    return tuple(found)


def oneoff_candidates(spec: LifeSpec, character_id: UUID, day: date, context: PlanContext) -> list[PlannedEvent]:
    events: list[PlannedEvent] = []
    for template in spec.oneoffs:
        if not oneoff_occurs_on(spec, template, character_id, day):
            continue
        start, end = interval_on(day, template.start, template.end)
        events.append(
            PlannedEvent(
                kind="oneoff",
                source="generator",
                source_key=template.source_key,
                title=template.title,
                starts_at=start,
                ends_at=end,
                generated_for=day,
                busyness=template.busyness,
                status_label=template.status_label,
                location=template.location,
                description=template.description,
                mood=template.mood,
                post_tags=template.post_tags,
                post_probability=template.post_probability,
                participants=_companions_in(f"{template.title} {template.description or ''}", context, spec),
                notable=True,
            )
        )
    return events


@lru_cache(maxsize=1024)
def seasonal_event_day(spec: LifeSpec, seasonal: SeasonalSpec, character_id: UUID, year: int) -> date | None:
    """行事の予定を入れる日（その年の期間の中の 1 日）。キャラ・行事・年ごとに決まる（キャッシュする）。"""
    window = seasonal_window(seasonal.key, year)
    if window is None:
        return None
    days = window.days()
    if len(days) == 1:
        return days[0]

    def free_from_work(d: date) -> bool:
        start, end = interval_on(d, seasonal.start, seasonal.end)
        for block in spec.routines_on(d):
            if not block.is_work:
                continue
            b_start, b_end = interval_on(d, block.start, block.end)
            if b_start < end and start < b_end:
                return False
        return True

    free = [d for d in days if free_from_work(d)]
    preferred = [d for d in free if is_day_off(d)] or free or [d for d in days if is_day_off(d)] or days
    return seeded_random(character_id, "seasonal", seasonal.key, year).choice(preferred)


def seasonal_candidates(spec: LifeSpec, character_id: UUID, day: date) -> list[PlannedEvent]:
    events: list[PlannedEvent] = []
    for seasonal in spec.seasonal:
        if seasonal_event_day(spec, seasonal, character_id, day.year) != day:
            continue
        start, end = interval_on(day, seasonal.start, seasonal.end)
        events.append(
            PlannedEvent(
                kind="seasonal",
                source="seasonal",
                source_key=seasonal.source_key,
                title=seasonal.title,
                starts_at=start,
                ends_at=end,
                generated_for=day,
                busyness=seasonal.busyness,
                status_label=seasonal.status_label,
                location=seasonal.location,
                description=seasonal.description,
                mood=seasonal.mood,
                post_tags=seasonal.post_tags,
                post_probability=seasonal.post_probability,
                notable=True,
            )
        )
    return events


def birthday_on(spec: LifeSpec, day: date) -> bool:
    if spec.birthday is None:
        return False
    month, dom = spec.birthday
    if (month, dom) == (2, 29) and not _is_leap(day.year):
        dom = 28
    return (day.month, day.day) == (month, dom)


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def birthday_candidates(spec: LifeSpec, day: date) -> list[PlannedEvent]:
    if not birthday_on(spec, day):
        return []
    start, end = interval_on(day, *BIRTHDAY_TIMES)
    return [
        PlannedEvent(
            kind="oneoff",
            source="generator",
            source_key=BIRTHDAY_SOURCE_KEY,
            title=BIRTHDAY_TITLE,
            starts_at=start,
            ends_at=end,
            generated_for=day,
            busyness=1,
            status_label=BIRTHDAY_LABEL,
            location=spec.home,
            mood=BIRTHDAY_MOOD,
            post_tags=BIRTHDAY_TAGS,
            post_probability=BIRTHDAY_POST_PROBABILITY,
            notable=True,
        )
    ]


@lru_cache(maxsize=8192)
def _base_candidates(spec: LifeSpec, character_id: UUID, day: date) -> tuple[PlannedEvent, ...]:
    return (
        *seasonal_candidates(spec, character_id, day),
        *birthday_candidates(spec, day),
        *oneoff_candidates(spec, character_id, day, PlanContext()),
        *routine_candidates(spec, day),
    )


def raw_candidates(
    spec: LifeSpec, character_id: UUID, day: date, context: PlanContext | None = None
) -> list[PlannedEvent]:
    """その日に始まる予定の候補（重なりの解決前）。LifeSpec は不変なので、(spec, キャラ, 日) でキャッシュする。"""
    base = _base_candidates(spec, character_id, day)
    if context is None or not context.companions:
        return list(base)
    return [
        replace(e, participants=_companions_in(f"{e.title} {e.description or ''}", context, spec))
        if e.kind == "oneoff" and e.source_key != BIRTHDAY_SOURCE_KEY
        else e
        for e in base
    ]


# ---------------------------------------------------------------------------
# 重なりの解決（置き換え）
# ---------------------------------------------------------------------------


def subtract_intervals(
    start: datetime, end: datetime, blockers: Sequence[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    """[start, end) から blockers の区間を取り除いた残り（開始順）。"""
    pieces: list[tuple[datetime, datetime]] = [(start, end)]
    for b_start, b_end in sorted(blockers):
        next_pieces: list[tuple[datetime, datetime]] = []
        for p_start, p_end in pieces:
            if b_end <= p_start or p_end <= b_start:
                next_pieces.append((p_start, p_end))
                continue
            if p_start < b_start:
                next_pieces.append((p_start, b_start))
            if b_end < p_end:
                next_pieces.append((b_end, p_end))
        pieces = next_pieces
    return pieces


def _place_routine(
    event: PlannedEvent, blockers: Sequence[tuple[datetime, datetime]], min_segment: timedelta
) -> list[PlannedEvent]:
    overlapping = [(s, e) for s, e in blockers if event.overlaps(s, e)]
    if not overlapping:
        return [event]
    pieces = [
        (s, e) for s, e in subtract_intervals(event.starts_at, event.ends_at, overlapping) if e - s >= min_segment
    ]
    base = event.segment
    if len(pieces) == 1:
        s, e = pieces[0]
        return [replace(event, starts_at=s, ends_at=e, segment=base if base is not None else 0)]
    return [
        replace(event, starts_at=s, ends_at=e, segment=(base * 10 + i) if base is not None else i)
        for i, (s, e) in enumerate(pieces)
    ]


def resolve(candidates: Sequence[PlannedEvent], *, min_segment: timedelta = MIN_ROUTINE_SEGMENT) -> list[PlannedEvent]:
    """優先度の高い予定から置き、ルーティンは重なりを切り取る。結果は重ならない（開始順）。"""
    placed: list[PlannedEvent] = []
    fixed = sorted(
        (c for c in candidates if c.kind != "routine"),
        key=lambda c: (c.priority, c.starts_at, c.source_key),
    )
    for event in fixed:
        if any(event.overlaps(p.starts_at, p.ends_at) for p in placed):
            continue
        placed.append(event)
    routines = sorted((c for c in candidates if c.kind == "routine"), key=lambda c: (c.starts_at, c.source_key))
    for event in routines:
        placed.extend(_place_routine(event, [(p.starts_at, p.ends_at) for p in placed], min_segment))
    return sorted(placed, key=lambda e: (e.starts_at, e.source_key))


def plan_day(spec: LifeSpec, character_id: UUID, day: date, context: PlanContext | None = None) -> list[PlannedEvent]:
    """日付 day（JST）に生成する予定。D-1〜D+1 の候補をまとめて解決し、D の分だけを返す。"""
    candidates: list[PlannedEvent] = []
    for offset in (-1, 0, 1):
        candidates.extend(raw_candidates(spec, character_id, day + timedelta(days=offset), context))
    return [e for e in resolve(candidates) if e.generated_for == day]


def plan_range(
    spec: LifeSpec, character_id: UUID, first: date, last: date, context: PlanContext | None = None
) -> list[PlannedEvent]:
    """first〜last（両端を含む）の予定。"""
    events: list[PlannedEvent] = []
    day = first
    while day <= last:
        events.extend(plan_day(spec, character_id, day, context))
        day += timedelta(days=1)
    return events


def clip_against_existing(
    planned: Sequence[PlannedEvent],
    existing: Sequence[tuple[datetime, datetime]],
    *,
    min_segment: timedelta = MIN_ROUTINE_SEGMENT,
) -> tuple[list[PlannedEvent], list[PlannedEvent]]:
    """DB にすでにある公開の予定（隣の日の生成分・手動の予定など）と重ならないようにする。

    既存の予定を優先する（すでに状態・記憶・投稿に使われている可能性があるため）。単発・行事は重なれば落とし、
    ルーティンは重なる部分を切り取る。戻り値は（残す予定, 落とした予定）。
    """
    kept: list[PlannedEvent] = []
    dropped: list[PlannedEvent] = []
    for event in planned:
        if event.kind == "routine":
            pieces = _place_routine(event, existing, min_segment)
            if not pieces:
                dropped.append(event)
            kept.extend(pieces)
        elif any(event.overlaps(s, e) for s, e in existing):
            dropped.append(event)
        else:
            kept.append(event)
    return kept, dropped


def jst_day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=JST)
    return start, start + timedelta(days=1)
