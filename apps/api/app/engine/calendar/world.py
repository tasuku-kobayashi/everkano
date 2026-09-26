"""世界の時計（仕様 §5 C1 / C4）: 日本時間・曜日・季節・時間帯・祝日・季節の行事。

すべてのキャラに共通で、DB を使わない純粋関数だけで構成する（評価ハーネスの早送りでも同じ結果になる）。

データの出どころと前提（ADR に記録する）:
- 祝日: `holidays.country_holidays("JP")`（内閣府「国民の祝日」の規則。振替休日・国民の休日を含む。
  春分・秋分の日は天文計算による推定）。祝日名は日本語。
- 中秋の名月（tsukimi）: 旧暦 8 月 15 日。国立天文台 暦計算室の公表値（2024〜2035 年）を表で持つ。
  表の範囲外の年は「お月見」を出さない（表を足すまで）。2033 年は旧暦 2033 年問題の影響を受けない
  （8 月 15 日の月は確定している）ものとして扱う。
- 節分: 立春の前日。2021 年以降、立春が 2/3 になる年（2025・2029・2033）は節分が 2/2 になる。
- 期間のある行事は、関東（東京）の平年値に寄せた「おおよその期間」で持つ:
  - 梅雨: 気象庁の関東甲信の平年値（梅雨入り 6/7 ごろ・梅雨明け 7/19 ごろ）→ 6/7〜7/19
  - 花見: 東京の桜の開花（平年 3/24 ごろ）〜満開後の散り際 → 3/25〜4/10
  - 紅葉: 東京・京都の見頃 → 11/15〜12/7
  - 夏祭り・花火: 7/20〜8/31（7 月下旬〜8 月に集中する）
  - お盆: 8/13〜8/16 / 年始: 1/1〜1/3 / 年末: 12/28〜12/31 / クリスマス: 12/24〜25
  - ゴールデンウィーク: 4/29〜5/5。5/6 以降が振替休日なら連続する祝日まで延ばす
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Final, Literal

import holidays

from app.engine.types import SEASONAL_KEYS, WorldState, to_jst

Season = Literal["spring", "summer", "autumn", "winter"]

WEEKDAYS_JA: Final[tuple[str, ...]] = ("月", "火", "水", "木", "金", "土", "日")
# ペルソナ YAML の曜日キー（datetime.weekday() の順）
WEEKDAY_KEYS: Final[tuple[str, ...]] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

SEASON_JA: Final[dict[str, str]] = {"spring": "春", "summer": "夏", "autumn": "秋", "winter": "冬"}

SEASONAL_LABELS_JA: Final[dict[str, str]] = {
    "new_year": "お正月",
    "setsubun": "節分",
    "valentine": "バレンタインデー",
    "white_day": "ホワイトデー",
    "hanami": "お花見の季節",
    "golden_week": "ゴールデンウィーク",
    "tsuyu": "梅雨",
    "tanabata": "七夕",
    "summer_festival": "夏祭り・花火の季節",
    "obon": "お盆",
    "tsukimi": "十五夜（お月見）",
    "halloween": "ハロウィン",
    "autumn_leaves": "紅葉の季節",
    "christmas": "クリスマス",
    "year_end": "年末",
}

# 中秋の名月（旧暦 8/15）。国立天文台 暦計算室の公表値
TSUKIMI_DATES: Final[dict[int, date]] = {
    2024: date(2024, 9, 17),
    2025: date(2025, 10, 6),
    2026: date(2026, 9, 25),
    2027: date(2027, 9, 15),
    2028: date(2028, 10, 3),
    2029: date(2029, 9, 22),
    2030: date(2030, 9, 12),
    2031: date(2031, 10, 1),
    2032: date(2032, 9, 19),
    2033: date(2033, 9, 8),
    2034: date(2034, 9, 27),
    2035: date(2035, 9, 16),
}

# 立春が 2/3 になり、節分が 2/2 になる年（それ以外は 2/3。2021〜2035 年の範囲で確認）
_SETSUBUN_FEB2_YEARS: Final[frozenset[int]] = frozenset({2021, 2025, 2029, 2033})

# 期間のある行事（月, 日）〜（月, 日）。両端を含む
_FIXED_WINDOWS: Final[dict[str, tuple[tuple[int, int], tuple[int, int]]]] = {
    "new_year": ((1, 1), (1, 3)),
    "valentine": ((2, 14), (2, 14)),
    "white_day": ((3, 14), (3, 14)),
    "hanami": ((3, 25), (4, 10)),
    "tsuyu": ((6, 7), (7, 19)),
    "tanabata": ((7, 7), (7, 7)),
    "summer_festival": ((7, 20), (8, 31)),
    "obon": ((8, 13), (8, 16)),
    "halloween": ((10, 31), (10, 31)),
    "autumn_leaves": ((11, 15), (12, 7)),
    "christmas": ((12, 24), (12, 25)),
    "year_end": ((12, 28), (12, 31)),
}


@dataclass(frozen=True, slots=True)
class SeasonalWindow:
    """行事の期間（JST の日付。両端を含む）。"""

    key: str
    start: date
    end: date

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def days(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1)]


@lru_cache(maxsize=64)
def _holidays_of_year(year: int) -> dict[date, str]:
    return dict(holidays.country_holidays("JP", years=year, language="ja").items())


def holiday_name(day: date) -> str | None:
    """日本の祝日名（振替休日・国民の休日を含む）。祝日でなければ None。"""
    return _holidays_of_year(day.year).get(day)


def is_day_off(day: date) -> bool:
    """土日祝（一般的な「休日」。キャラ個別の休みはペルソナのルーティンが決める）。"""
    return day.weekday() >= 5 or holiday_name(day) is not None


def season_of(day: date) -> Season:
    if day.month in (3, 4, 5):
        return "spring"
    if day.month in (6, 7, 8):
        return "summer"
    if day.month in (9, 10, 11):
        return "autumn"
    return "winter"


def time_of_day_ja(hour: int) -> str:
    """時間帯の呼び方（早朝 4〜6 / 朝 6〜10 / 昼 10〜16 / 夕方 16〜19 / 夜 19〜23 / 深夜 23〜4）。"""
    if 4 <= hour < 6:
        return "早朝"
    if 6 <= hour < 10:
        return "朝"
    if 10 <= hour < 16:
        return "昼"
    if 16 <= hour < 19:
        return "夕方"
    if 19 <= hour < 23:
        return "夜"
    return "深夜"


def _golden_week(year: int) -> SeasonalWindow:
    start, end = date(year, 4, 29), date(year, 5, 5)
    while holiday_name(end + timedelta(days=1)) is not None:
        end += timedelta(days=1)
    return SeasonalWindow("golden_week", start, end)


def seasonal_window(key: str, year: int) -> SeasonalWindow | None:
    """行事 `key` の `year` 年の期間。定義が無い（お月見の表の範囲外など）場合は None。"""
    if key == "golden_week":
        return _golden_week(year)
    if key == "setsubun":
        day = date(year, 2, 2 if year in _SETSUBUN_FEB2_YEARS else 3)
        return SeasonalWindow(key, day, day)
    if key == "tsukimi":
        moon = TSUKIMI_DATES.get(year)
        return SeasonalWindow(key, moon, moon) if moon is not None else None
    fixed = _FIXED_WINDOWS.get(key)
    if fixed is None:
        return None
    (m1, d1), (m2, d2) = fixed
    return SeasonalWindow(key, date(year, m1, d1), date(year, m2, d2))


def seasonal_keys_on(day: date) -> tuple[str, ...]:
    """その日が該当する行事のキー（SEASONAL_KEYS の順）。"""
    keys: list[str] = []
    for key in SEASONAL_KEYS:
        window = seasonal_window(key, day.year)
        if window is not None and window.contains(day):
            keys.append(key)
    return tuple(keys)


def weekday_key(day: date) -> str:
    return WEEKDAY_KEYS[day.weekday()]


def build_world_state(now: datetime) -> WorldState:
    """現在時刻（UTC）から世界の時計を作る。キャラ全員に共通。"""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local = to_jst(now)
    today = local.date()
    season = season_of(today)
    keys = seasonal_keys_on(today)
    return WorldState(
        now=now,
        now_jst=local,
        weekday_ja=WEEKDAYS_JA[today.weekday()],
        season=season,
        season_ja=SEASON_JA[season],
        time_of_day_ja=time_of_day_ja(local.hour),
        holiday_name=holiday_name(today),
        is_day_off=is_day_off(today),
        seasonal_keys=keys,
        seasonal_labels_ja=tuple(SEASONAL_LABELS_JA[k] for k in keys),
    )
