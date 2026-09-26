"""`persona.engine` が無いキャラの生活の推定（フォールバック）。

自由記述の `schedule_pattern`（例:「平日: 7:00起床 / 9:00出社 / 19:00退社 / 1:00就寝」）から、確実に読み取れる
最小限のルーティンだけを作る:
- 睡眠: 「就寝」の時刻 〜「起床」の時刻（無ければ既定の 00:30〜07:30。休日で「昼まで寝る」なら 02:00〜11:30）
- 仕事: 「出社・出勤」〜「退社・退勤・閉店」の時刻、または「11:00-16:00 喫茶店でアルバイト」のような時間帯つきの仕事
それ以外（カフェ・読書など）は予定にせず、既定の過ごし方（のんびり過ごしている）に任せる。性格と矛盾する
予定を作らないことを優先し、推定は控えめにする（キャラの一貫性 > 生活感）。

`schedule_pattern` も無いキャラ（YAML が無い `Persona.fallback()`）は、睡眠だけの既定の一日になる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

from app.engine.calendar.life import (
    ALL_DAY_KEYS,
    WORKDAY_KEYS,
    DefaultSpec,
    LifeSpec,
    RoutineSpec,
    compute_holiday_as_sunday,
    minutes_to_hhmm,
    routine_key,
)
from app.engine.calendar.world import WEEKDAY_KEYS
from app.services.llm import parse_schedule_days
from app.services.persona import Persona

DEFAULT_ACTIVITY: Final[DefaultSpec] = DefaultSpec(
    activity="のんびり過ごしている", location=None, status_label="のんびり中", busyness=0
)
SLEEP_ACTIVITY: Final[str] = "寝ている（睡眠）"
SLEEP_LABEL: Final[str] = "おやすみ中"
DEFAULT_SLEEP: Final[tuple[str, str]] = ("00:30", "07:30")
LATE_SLEEP: Final[tuple[str, str]] = ("02:00", "11:30")
DEFAULT_SLEEP_MINUTES: Final[int] = 7 * 60

_WEEKDAY_PREFIXES: Final[tuple[str, ...]] = ("平日",)
_HOLIDAY_PREFIXES: Final[tuple[str, ...]] = ("休日", "土日", "週末", "休み")
_HEAD: Final = re.compile(r"^[^:：\d]*[:：]")
_RANGE: Final = re.compile(
    r"^\s*(\d{1,2})(?:[:：](\d{2}))?\s*時?\s*[-〜~ー–]\s*(\d{1,2})(?:[:：](\d{2}))?\s*時?\s*(.*)$"
)
_POINT: Final = re.compile(r"^\s*(\d{1,2})(?:[:：](\d{2}))?\s*時?\s*(?:以降|から|頃|ごろ)?\s*(?:は|に)?\s*(.*)$")
_WAKE_WORDS: Final[tuple[str, ...]] = ("起床", "起き")
_SLEEP_WORDS: Final[tuple[str, ...]] = ("就寝", "寝る", "寝落ち")
_WORK_START_WORDS: Final[tuple[str, ...]] = ("出社", "出勤", "厨房入り", "会場入り")
_WORK_END_WORDS: Final[tuple[str, ...]] = ("退社", "退勤", "閉店", "終業")
_WORK_RANGE_WORDS: Final[tuple[str, ...]] = (
    "仕事",
    "勤務",
    "出勤",
    "バイト",
    "パート",
    "レッスン",
    "サロン",
    "店番",
    "店頭",
    "厨房",
    "展示",
)
_PARENS: Final = re.compile(r"[（(][^）)]*[）)]")


@dataclass
class _Line:
    is_holiday: bool
    days: frozenset[str] | None
    body: str
    points: list[tuple[int, str]] = field(default_factory=list)
    ranges: list[tuple[int, int, str]] = field(default_factory=list)


def _minutes(hour: str, minute: str | None) -> int:
    return int(hour) * 60 + int(minute or 0)


def _parse_line(raw: str) -> _Line | None:
    line = raw.strip()
    if not line.startswith(_WEEKDAY_PREFIXES + _HOLIDAY_PREFIXES):
        return None
    head = _HEAD.match(line)
    body = line[head.end() :] if head else line
    days_idx = parse_schedule_days(head.group(0)) if head else None
    days = frozenset(WEEKDAY_KEYS[i] for i in days_idx) if days_idx else None
    parsed = _Line(is_holiday=line.startswith(_HOLIDAY_PREFIXES), days=days, body=body)
    for seg in (s.strip() for s in re.split(r"[/／]", body)):
        if not seg:
            continue
        rng = _RANGE.match(seg)
        if rng:
            start = _minutes(rng.group(1), rng.group(2))
            end = _minutes(rng.group(3), rng.group(4))
            parsed.ranges.append((start, end, rng.group(5).strip()))
            continue
        point = _POINT.match(seg)
        if point and re.match(r"^\s*\d", seg):
            parsed.points.append((_minutes(point.group(1), point.group(2)), point.group(3).strip()))
    return parsed


def _clean_title(text: str, fallback: str) -> str:
    cleaned = _PARENS.sub("", text).strip(" 、。")
    return (cleaned or fallback)[:60]


def _work_label(text: str) -> str:
    if "バイト" in text:
        return "バイト中"
    if "レッスン" in text:
        return "レッスン中"
    if "パート" in text:
        return "パート中"
    return "仕事中"


def _routine(days: frozenset[str], start: int, end: int, *, activity: str, label: str, busyness: int) -> RoutineSpec:
    s, e = minutes_to_hhmm(start), minutes_to_hhmm(end % 1440 if end >= 1440 else end)
    return RoutineSpec(
        key=routine_key(activity, s, e, days),
        days=days,
        start=s,
        end=e,
        activity=activity,
        location=None,
        busyness=busyness,
        mood=None,
        status_label=label,
    )


def _blocks_for_line(line: _Line, days: frozenset[str]) -> list[RoutineSpec]:
    blocks: list[RoutineSpec] = []
    wake = next((m for m, text in line.points if any(w in text for w in _WAKE_WORDS)), None)
    sleep = next(
        (
            m
            for m, text in line.points
            if any(w in text for w in _SLEEP_WORDS) and not any(w in text for w in _WAKE_WORDS)
        ),
        None,
    )
    if sleep is not None:
        end = wake if wake is not None else (sleep + DEFAULT_SLEEP_MINUTES) % 1440
        blocks.append(_routine(days, sleep, end, activity=SLEEP_ACTIVITY, label=SLEEP_LABEL, busyness=3))
    elif wake is not None:
        start, end = (wake - DEFAULT_SLEEP_MINUTES) % 1440, wake
        blocks.append(_routine(days, start, end, activity=SLEEP_ACTIVITY, label=SLEEP_LABEL, busyness=3))
    else:
        late = line.is_holiday and ("昼まで" in line.body or "昼過ぎ" in line.body)
        s, e = LATE_SLEEP if late else DEFAULT_SLEEP
        blocks.append(
            RoutineSpec(
                key=routine_key(SLEEP_ACTIVITY, s, e, days),
                days=days,
                start=s,
                end=e,
                activity=SLEEP_ACTIVITY,
                location=None,
                busyness=3,
                mood=None,
                status_label=SLEEP_LABEL,
            )
        )
    # 仕事: 出社〜退社の時刻
    work_start = next(((m, t) for m, t in line.points if any(w in t for w in _WORK_START_WORDS)), None)
    if work_start is not None:
        work_end = next(
            (m for m, t in line.points if m > work_start[0] and any(w in t for w in _WORK_END_WORDS)),
            None,
        )
        if work_end is None:
            work_end = next(
                (s for s, _e, t in line.ranges if s > work_start[0] and any(w in t for w in _WORK_END_WORDS)),
                None,
            )
        if work_end is not None and work_end - work_start[0] >= 60:
            blocks.append(_routine(days, work_start[0], work_end, activity="仕事", label="仕事中", busyness=2))
    # 時間帯つきの仕事（「11:00-16:00 喫茶店でアルバイト」）
    for start, end, text in line.ranges:
        if not any(w in text for w in _WORK_RANGE_WORDS) or any(w in text for w in _WORK_END_WORDS):
            continue
        if end <= start:
            continue
        blocks.append(
            _routine(days, start, end, activity=_clean_title(text, "仕事"), label=_work_label(text), busyness=2)
        )
    return blocks


def fallback_life_spec(persona: Persona) -> LifeSpec:
    """`persona.engine` が無いキャラの LifeSpec。"""
    lines = [parsed for raw in persona.schedule_pattern.splitlines() if (parsed := _parse_line(raw)) is not None]
    routine: list[RoutineSpec] = []
    if lines:
        claimed: set[str] = set()
        for line in lines:
            if line.days is not None:
                claimed |= line.days
        weekday_days: frozenset[str] = frozenset()
        for line in lines:
            if line.is_holiday:
                continue
            days = line.days if line.days is not None else frozenset(WORKDAY_KEYS - claimed)
            weekday_days |= days
            if days:
                routine.extend(_blocks_for_line(line, days))
        for line in lines:
            if not line.is_holiday:
                continue
            days = line.days if line.days is not None else frozenset(ALL_DAY_KEYS - weekday_days)
            if days:
                routine.extend(_blocks_for_line(line, days))
        source = "schedule_pattern"
    else:
        s, e = DEFAULT_SLEEP
        routine.append(
            RoutineSpec(
                key=routine_key(SLEEP_ACTIVITY, s, e, ALL_DAY_KEYS),
                days=ALL_DAY_KEYS,
                start=s,
                end=e,
                activity=SLEEP_ACTIVITY,
                location=None,
                busyness=3,
                mood=None,
                status_label=SLEEP_LABEL,
            )
        )
        source = "default"
    return LifeSpec(
        persona_key=persona.key,
        name=persona.name,
        routine=tuple(routine),
        oneoffs=(),
        seasonal=(),
        default=DEFAULT_ACTIVITY,
        home=None,
        birthday=None,
        friend_names=(),
        holiday_as_sunday=compute_holiday_as_sunday(routine),
        source="schedule_pattern" if source == "schedule_pattern" else "default",
    )
