"""シミュレーションの暦と、相対的な日付表現の正解（エンジンの解決とは独立に計算する）。

- 1 日目 = `start`（JST の 0:00）。日は JST の暦日で数える。
- 「来週の X 曜」= 今日の週（月曜始まり）の次の週の X 曜。「今度の X 曜」= 今日より後の最初の X 曜。
  （日本語の一般的な解釈。エンジンの約束の期日の解決がこれと違えば、約束の回収率の内訳に現れる）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final

from app.engine.types import JST

# 既定の開始日: 2030-01-07（月）JST。共有の開発 DB の現在の日付と重ならない遠い未来の期間にする（EVAL_BRIEF）
DEFAULT_START: Final[datetime] = datetime(2030, 1, 7, 0, 0, tzinfo=JST)

WEEKDAYS_JA: Final[str] = "月火水木金土日"


def parse_start(value: str) -> datetime:
    """`2030-01-07` / `2030-01-07T00:00+09:00` → JST の日付の 0:00（タイムゾーンが無ければ JST とみなす）。"""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    local = parsed.astimezone(JST)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


@dataclass(frozen=True, slots=True)
class SimCalendar:
    """シミュレーションの暦。`start` は JST の 0:00（timezone-aware）。"""

    start: datetime
    days: int

    def __post_init__(self) -> None:
        if self.start.tzinfo is None:
            raise ValueError("SimCalendar.start must be timezone-aware")
        if self.days < 1:
            raise ValueError("days must be >= 1")

    @property
    def start_utc(self) -> datetime:
        return self.start.astimezone(UTC)

    @property
    def end(self) -> datetime:
        """最終日の翌日の 0:00（JST）。"""
        return self.day_start(self.days + 1)

    def day_start(self, day: int) -> datetime:
        return (self.start + timedelta(days=day - 1)).astimezone(JST)

    def date_of_day(self, day: int) -> date:
        return self.day_start(day).date()

    def at(self, day: int, hour: int, minute: int = 0) -> datetime:
        """day 日目の JST hour:minute（24 時以降は翌日に繰り越す）。UTC で返す。"""
        base = datetime.combine(self.date_of_day(day), time(0, 0), tzinfo=JST)
        return (base + timedelta(hours=hour, minutes=minute)).astimezone(UTC)

    def day_of(self, value: datetime) -> int:
        """JST の暦日で何日目か（開始日 = 1）。"""
        return (value.astimezone(JST).date() - self.start.date()).days + 1

    def day_of_date(self, value: date) -> int:
        return (value - self.start.date()).days + 1

    def weekday(self, day: int) -> int:
        """0 = 月曜。"""
        return self.date_of_day(day).weekday()


def next_week_weekday(today: date, weekday: int) -> date:
    """「来週の X 曜」: 今日の週（月曜始まり）の次の週の X 曜。"""
    monday = today - timedelta(days=today.weekday())
    return monday + timedelta(days=7 + weekday)


def upcoming_weekday(today: date, weekday: int) -> date:
    """「今度の X 曜」: 今日より後の最初の X 曜（今日が X 曜なら 1 週間後）。"""
    delta = (weekday - today.weekday()) % 7
    return today + timedelta(days=delta or 7)


def weekday_ja(value: date) -> str:
    return WEEKDAYS_JA[value.weekday()]
