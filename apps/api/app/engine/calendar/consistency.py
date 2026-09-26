"""予定の一貫性チェック（仕様 §5 C11 / §9.2「予定の一貫性: 同時刻の重複、性格と合わない予定の数 0 件」）。

テスト・評価ハーネスから使う。生成時の制約（generator.resolve / clip_against_existing）と DB の排他制約
（character_events_no_overlap）で壊れないはずのものを、外から検査する。

- error（評価の「0 件」の対象）:
  - overlap: 公開の予定が同じ時刻に 2 つある（同じ時間に 2 か所にいる）
  - not_from_template: 生成器の予定なのに、ペルソナのテンプレート（routine / events / seasonal / 誕生日）に無い
  - outside_template: テンプレートの曜日・月・時刻・行事の期間から外れている
  - min_interval: 同じ単発テンプレートが min_interval_days より短い間隔で起きている
  - memory_mismatch / memory_before_end / memory_cancelled_event: キャラ側の記憶（C9）が予定と食い違う
  - state_mismatch: character_states の状態が、その更新時刻の予定と違う
- warning（生活の妥当性。行事・単発で置き換わることがあるため 0 件は求めない）:
  - sleep_missing / sleep_short: 睡眠のルーティンがあるのに、その夜の睡眠が無い・3 時間未満
  - work_on_holiday: 平日だけ働く人が祝日に仕事をしている
  - not_sampled: 単発の予定が、今のテンプレートの抽選結果と一致しない（ペルソナの編集後など）
- lint_life_spec(): テンプレート自体の問題（ルーティンどうしの重なり・語彙に無い画像タグ）を返す（ペルソナ担当向け）。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Any, Final, Literal
from uuid import UUID

from app.engine.calendar.captions import IMAGE_TAGS
from app.engine.calendar.generator import (
    BIRTHDAY_SOURCE_KEY,
    birthday_on,
    oneoff_occurs_on,
    routine_candidates,
)
from app.engine.calendar.life import LifeSpec, interval_on, is_sleep_like
from app.engine.calendar.models import EventView
from app.engine.calendar.world import holiday_name, seasonal_window
from app.engine.types import jst_date

Severity = Literal["error", "warning"]

MIN_SLEEP: Final[timedelta] = timedelta(hours=3)
WORK_MIN: Final[timedelta] = timedelta(hours=4)
# 祝日の無い週（lint の基準週。2026-06-01 は月曜）
_LINT_WEEK_MONDAY: Final[date] = date(2026, 6, 1)


@dataclass(frozen=True, slots=True)
class ConsistencyViolation:
    code: str
    severity: Severity
    message: str
    character_id: UUID | None = None
    event_ids: tuple[UUID, ...] = ()
    at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "character_id": str(self.character_id) if self.character_id else None,
            "event_ids": [str(e) for e in self.event_ids],
            "at": self.at.isoformat() if self.at else None,
        }


@dataclass(frozen=True, slots=True)
class ConsistencyReport:
    characters: int
    events: int
    violations: tuple[ConsistencyViolation, ...] = ()
    per_character: dict[str, int] = field(default_factory=dict)  # キャラ ID → 予定の件数

    @property
    def errors(self) -> tuple[ConsistencyViolation, ...]:
        return tuple(v for v in self.violations if v.severity == "error")

    @property
    def warnings(self) -> tuple[ConsistencyViolation, ...]:
        return tuple(v for v in self.violations if v.severity == "warning")

    @property
    def ok(self) -> bool:
        return not self.errors

    def counts(self) -> dict[str, int]:
        return dict(Counter(v.code for v in self.violations))

    def to_dict(self) -> dict[str, Any]:
        return {
            "characters": self.characters,
            "events": self.events,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "counts": self.counts(),
            "violations": [v.to_dict() for v in self.violations],
        }


@dataclass(frozen=True, slots=True)
class CharacterMemoryRow:
    """キャラ側の記憶（予定由来の共有記憶の検査に使う列だけ）。"""

    id: UUID
    content: str
    occurred_at: datetime | None
    source_event_id: UUID | None
    created_at: datetime
    user_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class StateRow:
    character_id: UUID
    activity: str
    event_id: UUID | None
    updated_at: datetime


def _ids(*events: EventView) -> tuple[UUID, ...]:
    return tuple(e.id for e in events if e.id is not None)


def _label(event: EventView) -> str:
    return f"{event.title}（{event.starts_at.isoformat()}〜{event.ends_at.isoformat()}）"


# ---------------------------------------------------------------------------
# 予定
# ---------------------------------------------------------------------------


def check_overlaps(events: Sequence[EventView], character_id: UUID | None = None) -> list[ConsistencyViolation]:
    """公開・有効な予定の時間の重なり（同じ時間に 2 か所にいない）。"""
    active = sorted((e for e in events if e.is_public and e.is_active), key=lambda e: (e.starts_at, e.ends_at))
    violations: list[ConsistencyViolation] = []
    latest: EventView | None = None
    for event in active:
        if latest is not None and event.starts_at < latest.ends_at:
            violations.append(
                ConsistencyViolation(
                    code="overlap",
                    severity="error",
                    message=f"予定が重なっています: {_label(latest)} と {_label(event)}",
                    character_id=character_id,
                    event_ids=_ids(latest, event),
                    at=event.starts_at,
                )
            )
        if latest is None or event.ends_at > latest.ends_at:
            latest = event
    return violations


def _within(event: EventView, start: datetime, end: datetime) -> bool:
    return start <= event.starts_at and event.ends_at <= end


def check_template_derivation(
    events: Sequence[EventView], spec: LifeSpec, character_id: UUID
) -> list[ConsistencyViolation]:
    """生成器の予定が、ペルソナのテンプレートから作られたものか（性格と合わない予定が無いか）。"""
    routines = spec.routine_by_key()
    oneoffs = spec.oneoff_by_key()
    seasonals = spec.seasonal_by_key()
    violations: list[ConsistencyViolation] = []

    def add(code: str, event: EventView, message: str, severity: Severity = "error") -> None:
        violations.append(
            ConsistencyViolation(
                code=code,
                severity=severity,
                message=f"{message}: {_label(event)}",
                character_id=character_id,
                event_ids=_ids(event),
                at=event.starts_at,
            )
        )

    for event in events:
        if event.source not in ("generator", "seasonal") or not event.is_active:
            continue
        key = event.source_key or ""
        day = event.generated_for or jst_date(event.starts_at)
        if event.kind == "routine":
            block = routines.get(key)
            if block is None:
                add("not_from_template", event, "ペルソナのルーティンに無い予定")
                continue
            start, end = interval_on(day, block.start, block.end)
            if spec.effective_weekday(day) not in block.days or not _within(event, start, end):
                add("outside_template", event, "ルーティンの曜日・時間帯の外")
            elif event.title != block.activity:
                add("outside_template", event, "ルーティンと内容が違う")
        elif key == BIRTHDAY_SOURCE_KEY:
            if not birthday_on(spec, day):
                add("outside_template", event, "誕生日ではない日の誕生日の予定")
        elif event.kind == "oneoff":
            template = oneoffs.get(key)
            if template is None:
                add("not_from_template", event, "ペルソナの単発テンプレートに無い予定")
                continue
            start, end = interval_on(day, template.start, template.end)
            if (
                spec.effective_weekday(day) not in template.days
                or (template.months is not None and day.month not in template.months)
                or (event.starts_at, event.ends_at) != (start, end)
                or event.title != template.title
            ):
                add("outside_template", event, "単発テンプレートの曜日・月・時刻の外")
            elif not oneoff_occurs_on(spec, template, character_id, day):
                add("not_sampled", event, "今のテンプレートの抽選結果と一致しない", "warning")
        elif event.kind == "seasonal":
            seasonal = seasonals.get(key)
            if seasonal is None:
                add("not_from_template", event, "ペルソナが参加しない行事の予定")
                continue
            window = seasonal_window(seasonal.key, day.year)
            start, end = interval_on(day, seasonal.start, seasonal.end)
            if window is None or not window.contains(day) or (event.starts_at, event.ends_at) != (start, end):
                add("outside_template", event, "行事の期間・時刻の外")
        else:
            add("not_from_template", event, f"生成器が作らない種類の予定（{event.kind}）")
    return violations


def check_min_interval(events: Sequence[EventView], spec: LifeSpec, character_id: UUID) -> list[ConsistencyViolation]:
    violations: list[ConsistencyViolation] = []
    templates = spec.oneoff_by_key()
    by_key: dict[str, list[EventView]] = {}
    for event in events:
        if event.kind == "oneoff" and event.is_active and event.source_key in templates:
            by_key.setdefault(event.source_key, []).append(event)
    for key, occurrences in by_key.items():
        interval = templates[key].min_interval_days
        if interval <= 0:
            continue
        days = sorted({(e.generated_for or jst_date(e.starts_at)) for e in occurrences})
        for earlier, later in pairwise(days):
            if (later - earlier).days < interval:
                violations.append(
                    ConsistencyViolation(
                        code="min_interval",
                        severity="error",
                        message=f"{key} が {earlier}〜{later} の間隔（{interval}日未満）で起きている",
                        character_id=character_id,
                    )
                )
    return violations


def _merged_sleep(events: Sequence[EventView]) -> list[tuple[datetime, datetime]]:
    """睡眠の予定を、隙間なく続くものどうしでつなげた区間（前日からの睡眠の続きを 1 回の睡眠として数える）。"""
    intervals = sorted(
        (e.starts_at, e.ends_at)
        for e in events
        if e.kind == "routine" and e.is_active and is_sleep_like(e.title, e.status_label)
    )
    merged: list[tuple[datetime, datetime]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def check_sleep(
    events: Sequence[EventView], spec: LifeSpec, character_id: UUID, first: date, last: date
) -> list[ConsistencyViolation]:
    """睡眠のルーティンがある日に、その時間帯の睡眠が（ほぼ）無くなっていないか（warning）。"""
    violations: list[ConsistencyViolation] = []
    merged = _merged_sleep(events)
    day = first
    while day <= last:
        for block in (b for b in spec.routines_on(day) if b.is_sleep):
            start, end = interval_on(day, block.start, block.end)
            overlapping = [(s, e) for s, e in merged if s < end and start < e]
            if not overlapping:
                violations.append(
                    ConsistencyViolation(
                        code="sleep_missing",
                        severity="warning",
                        message=f"{day} の睡眠がありません（単発・行事で置き換わった）",
                        character_id=character_id,
                        at=start,
                    )
                )
                continue
            longest = max(e - s for s, e in overlapping)
            if longest < MIN_SLEEP:
                violations.append(
                    ConsistencyViolation(
                        code="sleep_short",
                        severity="warning",
                        message=f"{day} の睡眠が {longest} しかありません",
                        character_id=character_id,
                        at=start,
                    )
                )
        day += timedelta(days=1)
    return violations


def check_holiday_work(events: Sequence[EventView], spec: LifeSpec, character_id: UUID) -> list[ConsistencyViolation]:
    if not spec.holiday_as_sunday:
        return []
    violations: list[ConsistencyViolation] = []
    for event in events:
        if event.kind != "routine" or not event.is_active or event.busyness < 2:
            continue
        if is_sleep_like(event.title, event.status_label):
            continue
        day = event.generated_for or jst_date(event.starts_at)
        if event.ends_at - event.starts_at >= WORK_MIN and holiday_name(day) is not None:
            violations.append(
                ConsistencyViolation(
                    code="work_on_holiday",
                    severity="warning",
                    message=f"祝日（{holiday_name(day)}）に仕事: {_label(event)}",
                    character_id=character_id,
                    event_ids=_ids(event),
                    at=event.starts_at,
                )
            )
    return violations


# ---------------------------------------------------------------------------
# キャラ側の記憶・状態
# ---------------------------------------------------------------------------


def check_memories(
    events: Sequence[EventView], memories: Iterable[CharacterMemoryRow], character_id: UUID
) -> list[ConsistencyViolation]:
    """予定由来のキャラ側の記憶（C9）が、元の予定と食い違わないか。"""
    by_id = {e.id: e for e in events if e.id is not None}
    violations: list[ConsistencyViolation] = []
    for memory in memories:
        if memory.source_event_id is None:
            continue
        event = by_id.get(memory.source_event_id)
        if event is None:
            continue
        if not event.is_active:
            code, message = "memory_cancelled_event", "取り消された予定の記憶"
        elif memory.occurred_at is not None and memory.occurred_at != event.starts_at:
            code, message = "memory_mismatch", "記憶の日時が予定と違う"
        elif event.title not in memory.content:
            code, message = "memory_mismatch", "記憶の内容が予定と違う"
        elif memory.created_at < event.ends_at:
            code, message = "memory_before_end", "予定が終わる前に記憶になっている"
        else:
            continue
        violations.append(
            ConsistencyViolation(
                code=code,
                severity="error",
                message=f"{message}: {memory.content}",
                character_id=character_id,
                event_ids=_ids(event),
                at=memory.created_at,
            )
        )
    return violations


def check_state(state: StateRow | None, events: Sequence[EventView]) -> list[ConsistencyViolation]:
    """キャラの状態（キャッシュ）が、その更新時刻に実際に入っていた予定と一致するか。"""
    if state is None:
        return []
    covering = next(
        (
            e
            for e in events
            if e.is_public
            and e.is_active
            and e.covers(state.updated_at)
            and e.id is not None
            # 状態を書いた後に生成された予定（生成の遅れ）とは比べない
            and (e.created_at is None or e.created_at <= state.updated_at)
        ),
        None,
    )
    expected = covering.id if covering is not None else None
    if expected == state.event_id:
        return []
    return [
        ConsistencyViolation(
            code="state_mismatch",
            severity="error",
            message=(
                f"状態「{state.activity}」（予定 {state.event_id}）が {state.updated_at.isoformat()} の予定"
                f"（{covering.title if covering else 'なし'}）と一致しない"
            ),
            character_id=state.character_id,
            event_ids=tuple(i for i in (state.event_id, expected) if i is not None),
            at=state.updated_at,
        )
    ]


def check_character(
    spec: LifeSpec,
    events: Sequence[EventView],
    *,
    character_id: UUID,
    first: date,
    last: date,
    memories: Iterable[CharacterMemoryRow] = (),
    state: StateRow | None = None,
) -> list[ConsistencyViolation]:
    """1 キャラの予定（first〜last の生成分を含む）をまとめて検査する。"""
    in_range = [e for e in events if first <= (e.generated_for or jst_date(e.starts_at)) <= last]
    return [
        *check_overlaps(events, character_id),
        *check_template_derivation(in_range, spec, character_id),
        *check_min_interval(in_range, spec, character_id),
        *check_sleep(events, spec, character_id, first, last),
        *check_holiday_work(in_range, spec, character_id),
        *check_memories(events, memories, character_id),
        *check_state(state, events),
    ]


# ---------------------------------------------------------------------------
# テンプレートの lint（ペルソナ担当向け）
# ---------------------------------------------------------------------------


def lint_life_spec(spec: LifeSpec) -> list[ConsistencyViolation]:
    """ルーティンどうしの重なり（後のものが切り取られる）と、語彙に無い画像タグを warning で返す。"""
    violations: list[ConsistencyViolation] = []
    candidates = []
    for offset in range(8):
        candidates.extend(routine_candidates(spec, _LINT_WEEK_MONDAY + timedelta(days=offset)))
    candidates.sort(key=lambda e: e.starts_at)
    seen: set[tuple[str, str]] = set()
    for i, a in enumerate(candidates):
        for b in candidates[i + 1 :]:
            if b.starts_at >= a.ends_at:
                break
            pair = (a.source_key, b.source_key)
            if pair in seen:
                continue
            seen.add(pair)
            violations.append(
                ConsistencyViolation(
                    code="routine_overlap",
                    severity="warning",
                    message=(
                        f"ルーティンが重なっています（後の方が切り取られます）: 「{a.title}」{a.starts_at:%a %H:%M}〜"
                        f"{a.ends_at:%H:%M} と「{b.title}」{b.starts_at:%a %H:%M}〜{b.ends_at:%H:%M}"
                    ),
                )
            )
    tags: list[tuple[str, str]] = [(r.activity, t) for r in spec.routine for t in r.post_tags]
    tags += [(o.title, t) for o in spec.oneoffs for t in o.post_tags]
    tags += [(s.title, t) for s in spec.seasonal for t in s.post_tags]
    for owner, tag in tags:
        if tag not in IMAGE_TAGS:
            violations.append(
                ConsistencyViolation(
                    code="unknown_tag", severity="warning", message=f"「{owner}」の画像タグ {tag} は語彙にありません"
                )
            )
    return violations
