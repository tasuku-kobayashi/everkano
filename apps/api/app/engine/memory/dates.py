"""相対的な日付表現の解決（日本時間）。約束の期日（M6）とキャラの発言の日付（M8）に使う。

- 週は月曜始まり（日本の「来週」= 次の月曜〜日曜）。
- 「今度の土曜」「土曜に」は今日より後の最初のその曜日。「週末」は土曜。
- 「10月1日」「10/1」は今年の日付。7日より前に過ぎているなら来年とみなす。
- 期日の時刻（due_at）は、日付だけ分かる場合は日本時間のその日の 12:00（promises.due_at の定義）。
- 現在時刻は必ず引数で受け取る（評価ハーネスの時間の早送り）。

LLM（memory_analysis）は絶対日付を返すので、本番の期日は LLM が解決する。ここはモックの期日解決と、
プロンプトに渡す「日付の早見表」（来週・再来週の範囲）に使う。
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final

from app.engine.memory.text import WEEKDAYS_JA
from app.engine.types import JST, DuePrecision

_WD: Final[str] = "月火水木金土日"
DAY_DUE_TIME: Final[time] = time(12, 0)


@dataclass(frozen=True, slots=True)
class ResolvedDate:
    date: date
    precision: DuePrecision
    time: time | None = None
    expression: str = ""  # 解決に使った表現（本文から取り除くため）


def normalize(text: str) -> str:
    """全角数字・記号を半角にそろえる（「１０月１日」→「10月1日」）。"""
    return unicodedata.normalize("NFKC", text)


def today_jst(now: datetime) -> date:
    return now.astimezone(JST).date()


def week_monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def next_weekday(today: date, weekday: int, *, include_today: bool = False) -> date:
    """今日より後（include_today なら今日を含む）の最初のその曜日。"""
    delta = (weekday - today.weekday()) % 7
    if delta == 0 and not include_today:
        delta = 7
    return today + timedelta(days=delta)


def add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _infer_year(today: date, month: int, day: int) -> date | None:
    candidate = _safe_date(today.year, month, day)
    if candidate is None:
        return None
    if candidate < today - timedelta(days=7):
        return _safe_date(today.year + 1, month, day)
    return candidate


# --- 時刻 -----------------------------------------------------------------------------
_TIME_RE: Final = re.compile(
    r"(午前|午後|朝|夜|夕方|昼|晩)?\s*(\d{1,2})\s*(?:時(?!間)\s*(?:(\d{1,2})\s*分|(半))?|:(\d{2}))"
)


def _parse_time(text: str) -> tuple[time | None, str]:
    match = _TIME_RE.search(text)
    if match is None:
        return None, ""
    period, hour_s, minute_s, half, colon_minute_s = match.groups()
    hour = int(hour_s)
    minute = int(colon_minute_s) if colon_minute_s else (int(minute_s) if minute_s else (30 if half else 0))
    if hour > 24 or minute > 59:
        return None, ""
    if period in {"午後", "夜", "夕方", "晩"} and hour < 12:
        hour += 12
    if hour == 24:
        hour = 0
    return time(hour, minute), match.group(0)


# --- 日付の表現（上から順に試す。長い表現を先に） ------------------------------------
_YMD_RE: Final = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_MD_RE: Final = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_SLASH_RE: Final = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])")
_REL_MONTH_DAY_RE: Final = re.compile(r"(来月|今月)の?\s*(\d{1,2})\s*日")
_WEEK_WD_RE: Final = re.compile(r"(再来週|来週|今週|次の|今度の|この)\s*の?\s*([月火水木金土日])曜日?")
_WEEKEND_RE: Final = re.compile(r"(再来週末|来週末|今週末|週末|土日)")
_DAY_WORD_RE: Final = re.compile(r"(明々後日|しあさって|明後日|あさって|明日|あした|今日|きょう|今夜|今晩|今朝)")
_AFTER_RE: Final = re.compile(r"(\d{1,3})\s*(日|週間|[ヶかカケ箇]?月)後")
_WEEK_ONLY_RE: Final = re.compile(r"(再来週|来週)")
_MONTH_ONLY_RE: Final = re.compile(r"(来月|月末)")
_BARE_WD_RE: Final = re.compile(r"(?<![今来週次度の])([月火水木金土日])曜日?")
_BARE_DAY_RE: Final = re.compile(r"(?<![\d月/])(\d{1,2})\s*日(?![間中目前後ぶ\d])")

_DAY_WORD_OFFSETS: Final[dict[str, int]] = {
    "明々後日": 3,
    "しあさって": 3,
    "明後日": 2,
    "あさって": 2,
    "明日": 1,
    "あした": 1,
    "今日": 0,
    "きょう": 0,
    "今夜": 0,
    "今晩": 0,
    "今朝": 0,
}


def _with_time(resolved: ResolvedDate, text: str) -> ResolvedDate:
    parsed, _ = _parse_time(text)
    if parsed is None or resolved.precision not in {"day", "datetime"}:
        return resolved
    return ResolvedDate(
        date=resolved.date,
        precision="datetime",
        time=parsed,
        expression=resolved.expression,
    )


def resolve_future_date(text: str, now: datetime) -> ResolvedDate | None:
    """文中の予定の日付を解決する（見つからなければ None）。過去の日付を返すこともある（呼び出し側で判定）。"""
    value = normalize(text)
    today = today_jst(now)
    resolved = _resolve_date(value, today)
    if resolved is None:
        return None
    return _with_time(resolved, value)


def _resolve_date(value: str, today: date) -> ResolvedDate | None:
    if (m := _YMD_RE.search(value)) is not None:
        found = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if found is not None:
            return ResolvedDate(found, "day", expression=m.group(0))
    if (m := _REL_MONTH_DAY_RE.search(value)) is not None:
        base = add_months(today.replace(day=1), 1 if m.group(1) == "来月" else 0)
        found = _safe_date(base.year, base.month, int(m.group(2)))
        if found is not None:
            return ResolvedDate(found, "day", expression=m.group(0))
    if (m := _MD_RE.search(value)) is not None:
        found = _infer_year(today, int(m.group(1)), int(m.group(2)))
        if found is not None:
            return ResolvedDate(found, "day", expression=m.group(0))
    if (m := _SLASH_RE.search(value)) is not None:
        month, day = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            found = _infer_year(today, month, day)
            if found is not None:
                return ResolvedDate(found, "day", expression=m.group(0))
    if (m := _WEEK_WD_RE.search(value)) is not None:
        qualifier, weekday = m.group(1), _WD.index(m.group(2))
        if qualifier in {"今週", "来週", "再来週"}:
            weeks = {"今週": 0, "来週": 1, "再来週": 2}[qualifier]
            found = week_monday(today) + timedelta(days=7 * weeks + weekday)
        else:
            found = next_weekday(today, weekday)
        return ResolvedDate(found, "day", expression=m.group(0))
    if (m := _WEEKEND_RE.search(value)) is not None:
        word = m.group(1)
        if word in {"来週末", "再来週末"}:
            weeks = 2 if word == "再来週末" else 1
            found = week_monday(today) + timedelta(days=7 * weeks + 5)
        elif today.weekday() == 6:
            # 日曜に「週末」と言えば次の週末
            found = today + timedelta(days=6)
        else:
            found = next_weekday(today, 5, include_today=True)
        return ResolvedDate(found, "day", expression=word)
    if (m := _DAY_WORD_RE.search(value)) is not None:
        found = today + timedelta(days=_DAY_WORD_OFFSETS[m.group(1)])
        return ResolvedDate(found, "day", expression=m.group(0))
    if (m := _AFTER_RE.search(value)) is not None:
        amount, unit = int(m.group(1)), m.group(2)
        if unit == "日":
            return ResolvedDate(today + timedelta(days=amount), "day", expression=m.group(0))
        if unit == "週間":
            return ResolvedDate(today + timedelta(days=7 * amount), "day", expression=m.group(0))
        return ResolvedDate(add_months(today, amount), "month", expression=m.group(0))
    if (m := _BARE_WD_RE.search(value)) is not None:
        found = next_weekday(today, _WD.index(m.group(1)))
        return ResolvedDate(found, "day", expression=m.group(0))
    if (m := _WEEK_ONLY_RE.search(value)) is not None:
        weeks = 2 if m.group(1) == "再来週" else 1
        return ResolvedDate(week_monday(today) + timedelta(days=7 * weeks), "week", expression=m.group(0))
    if (m := _MONTH_ONLY_RE.search(value)) is not None:
        if m.group(1) == "月末":
            last = calendar.monthrange(today.year, today.month)[1]
            return ResolvedDate(today.replace(day=last), "day", expression=m.group(0))
        return ResolvedDate(add_months(today.replace(day=1), 1), "month", expression=m.group(0))
    if (m := _BARE_DAY_RE.search(value)) is not None:
        day = int(m.group(1))
        if 1 <= day <= 31:
            this_month = _safe_date(today.year, today.month, day)
            if this_month is not None and this_month >= today:
                return ResolvedDate(this_month, "day", expression=m.group(0))
            base = add_months(today.replace(day=1), 1)
            found = _safe_date(base.year, base.month, day)
            if found is not None:
                return ResolvedDate(found, "day", expression=m.group(0))
    return None


_PAST_RE: Final = re.compile(r"(一昨日|おととい|昨日|きのう|昨夜|昨晩|ゆうべ|今朝|さっき|今日|きょう|先週|先月)")
_PAST_OFFSETS: Final[dict[str, int]] = {
    "一昨日": -2,
    "おととい": -2,
    "昨日": -1,
    "きのう": -1,
    "昨夜": -1,
    "昨晩": -1,
    "ゆうべ": -1,
    "今朝": 0,
    "さっき": 0,
    "今日": 0,
    "きょう": 0,
    "先週": -7,
}


def resolve_past_date(text: str, now: datetime) -> tuple[date, str] | None:
    """「昨日カフェに行った」の「昨日」など、過去の出来事の日付（おおよそ）と表現。先月は日付を決めない。"""
    match = _PAST_RE.search(normalize(text))
    if match is None or match.group(1) not in _PAST_OFFSETS:
        return None
    return today_jst(now) + timedelta(days=_PAST_OFFSETS[match.group(1)]), match.group(1)


def due_at(day: date | None, at: time | None, precision: DuePrecision) -> datetime | None:
    """期日（UTC）。日付だけなら日本時間の 12:00。unknown / 日付なしは None。"""
    if day is None or precision == "unknown":
        return None
    local_time = at if (precision == "datetime" and at is not None) else DAY_DUE_TIME
    return datetime.combine(day, local_time, tzinfo=JST).astimezone(UTC)


def date_reference(now: datetime, *, days: int = 14) -> str:
    """プロンプト用の日付の早見表（相対的な日付を絶対日付に直すため）。"""
    today = today_jst(now)
    monday = week_monday(today)
    lines = [
        f"- 今日: {_label(today)} / 明日: {_label(today + timedelta(days=1))}"
        f" / 明後日: {_label(today + timedelta(days=2))}",
        f"- 今週: {_label(monday)}〜{_label(monday + timedelta(days=6))}",
        f"- 来週: {_label(monday + timedelta(days=7))}〜{_label(monday + timedelta(days=13))}",
        f"- 再来週: {_label(monday + timedelta(days=14))}〜{_label(monday + timedelta(days=20))}",
        "- 今後の日付: " + " ".join(_label(today + timedelta(days=i)) for i in range(days)),
    ]
    return "\n".join(lines)


def _label(day: date) -> str:
    return f"{day.isoformat()}({WEEKDAYS_JA[day.weekday()]})"
