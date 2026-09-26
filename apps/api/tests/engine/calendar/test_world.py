"""世界の時計（C1 / C4）: 日本時間・曜日・季節・時間帯・祝日（振替休日を含む）・季節の行事。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.engine.calendar.world import (
    TSUKIMI_DATES,
    build_world_state,
    holiday_name,
    is_day_off,
    season_of,
    seasonal_keys_on,
    seasonal_window,
    time_of_day_ja,
)
from app.engine.types import JST, SEASONAL_KEYS


def jst(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=JST)


@pytest.mark.parametrize(
    ("day", "name"),
    [
        (date(2026, 1, 1), "元日"),
        (date(2026, 1, 12), "成人の日"),
        (date(2026, 5, 6), "振替休日"),  # 5/3（日）の振替
        (date(2026, 9, 21), "敬老の日"),
        (date(2026, 9, 22), "国民の休日"),  # 敬老の日と秋分の日に挟まれた日
        (date(2026, 9, 23), "秋分の日"),
        (date(2027, 3, 22), "振替休日"),  # 春分の日（3/21 日曜）の振替
        (date(2026, 11, 23), "勤労感謝の日"),
    ],
)
def test_japanese_holidays_including_substitute(day: date, name: str) -> None:
    assert holiday_name(day) == name
    assert is_day_off(day)


def test_regular_days() -> None:
    assert holiday_name(date(2026, 9, 24)) is None  # 木曜
    assert not is_day_off(date(2026, 9, 24))
    assert is_day_off(date(2026, 9, 26))  # 土曜
    assert holiday_name(date(2026, 12, 25)) is None  # クリスマスは祝日ではない


def test_world_state_for_a_saturday_night() -> None:
    world = build_world_state(jst(2026, 9, 26, 21, 30))
    assert world.now.tzinfo is not None
    assert world.now_jst.hour == 21
    assert world.weekday_ja == "土"
    assert world.season == "autumn"
    assert world.season_ja == "秋"
    assert world.time_of_day_ja == "夜"
    assert world.holiday_name is None
    assert world.is_day_off
    assert world.seasonal_keys == ()


def test_world_state_uses_jst_date_from_utc_now() -> None:
    # 2026-09-25 15:30 UTC = 2026-09-26 00:30 JST（日付・曜日・時間帯は JST で判定する）
    world = build_world_state(datetime(2026, 9, 25, 15, 30, tzinfo=UTC))
    assert world.now_jst.date() == date(2026, 9, 26)
    assert world.weekday_ja == "土"
    assert world.time_of_day_ja == "深夜"
    # 中秋の名月（9/25）は JST では前日なので含まれない
    assert "tsukimi" not in world.seasonal_keys


def test_world_state_holiday_and_season_labels() -> None:
    world = build_world_state(jst(2026, 5, 6, 8))
    assert world.holiday_name == "振替休日"
    assert world.is_day_off
    assert world.weekday_ja == "水"
    assert world.season == "spring"
    assert world.time_of_day_ja == "朝"
    assert "golden_week" in world.seasonal_keys
    assert "ゴールデンウィーク" in world.seasonal_labels_ja


def test_year_boundary() -> None:
    eve = build_world_state(jst(2026, 12, 31, 23, 59))
    new = build_world_state(jst(2026, 12, 31, 23, 59) + timedelta(minutes=2))
    assert eve.seasonal_keys == ("year_end",)
    assert eve.season == "winter"
    assert eve.holiday_name is None
    assert new.now_jst.date() == date(2027, 1, 1)
    assert new.seasonal_keys == ("new_year",)
    assert new.holiday_name == "元日"
    assert new.season == "winter"
    assert new.time_of_day_ja == "深夜"


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        build_world_state(datetime(2026, 9, 26, 12, 0))


@pytest.mark.parametrize(
    ("hour", "label"),
    [
        (0, "深夜"),
        (3, "深夜"),
        (4, "早朝"),
        (6, "朝"),
        (9, "朝"),
        (10, "昼"),
        (15, "昼"),
        (16, "夕方"),
        (19, "夜"),
        (23, "深夜"),
    ],
)
def test_time_of_day(hour: int, label: str) -> None:
    assert time_of_day_ja(hour) == label


@pytest.mark.parametrize(
    ("month", "season"),
    [
        (1, "winter"),
        (2, "winter"),
        (3, "spring"),
        (5, "spring"),
        (6, "summer"),
        (8, "summer"),
        (9, "autumn"),
        (11, "autumn"),
        (12, "winter"),
    ],
)
def test_season(month: int, season: str) -> None:
    assert season_of(date(2026, month, 15)) == season


@pytest.mark.parametrize(
    ("day", "expected", "absent"),
    [
        (date(2026, 1, 3), "new_year", None),
        (date(2026, 1, 4), None, "new_year"),
        (date(2026, 2, 3), "setsubun", None),
        (date(2029, 2, 2), "setsubun", None),  # 立春が 2/3 の年は節分が 2/2
        (date(2029, 2, 3), None, "setsubun"),
        (date(2026, 2, 14), "valentine", None),
        (date(2026, 3, 14), "white_day", None),
        (date(2026, 3, 24), None, "hanami"),
        (date(2026, 3, 25), "hanami", None),
        (date(2026, 4, 10), "hanami", None),
        (date(2026, 4, 11), None, "hanami"),
        (date(2026, 6, 6), None, "tsuyu"),
        (date(2026, 6, 7), "tsuyu", None),
        (date(2026, 7, 7), "tanabata", None),
        (date(2026, 7, 19), "tsuyu", None),
        (date(2026, 7, 20), "summer_festival", "tsuyu"),
        (date(2026, 8, 15), "obon", None),
        (date(2026, 8, 15), "summer_festival", None),
        (date(2026, 9, 25), "tsukimi", None),
        (date(2026, 9, 26), None, "tsukimi"),
        (date(2033, 9, 8), "tsukimi", None),
        (date(2026, 10, 31), "halloween", None),
        (date(2026, 11, 14), None, "autumn_leaves"),
        (date(2026, 11, 15), "autumn_leaves", None),
        (date(2026, 12, 7), "autumn_leaves", None),
        (date(2026, 12, 24), "christmas", None),
        (date(2026, 12, 25), "christmas", None),
        (date(2026, 12, 26), None, "christmas"),
        (date(2026, 12, 28), "year_end", None),
    ],
)
def test_seasonal_keys(day: date, expected: str | None, absent: str | None) -> None:
    keys = seasonal_keys_on(day)
    if expected is not None:
        assert expected in keys
    if absent is not None:
        assert absent not in keys


def test_golden_week_extends_over_substitute_holiday() -> None:
    gw_2026 = seasonal_window("golden_week", 2026)
    assert gw_2026 is not None
    assert (gw_2026.start, gw_2026.end) == (date(2026, 4, 29), date(2026, 5, 6))
    gw_2027 = seasonal_window("golden_week", 2027)
    assert gw_2027 is not None
    assert gw_2027.end == date(2027, 5, 5)


def test_tsukimi_table_covers_2026_to_2035_in_mid_autumn() -> None:
    for year in range(2026, 2036):
        moon = TSUKIMI_DATES[year]
        assert moon.year == year
        assert date(year, 9, 7) <= moon <= date(year, 10, 8)
        window = seasonal_window("tsukimi", year)
        assert window is not None
        assert window.start == window.end == moon
    assert seasonal_window("tsukimi", 2040) is None  # 表の範囲外は出さない


def test_every_seasonal_key_has_a_window_each_year() -> None:
    for year in range(2026, 2036):
        for key in SEASONAL_KEYS:
            window = seasonal_window(key, year)
            assert window is not None, (key, year)
            assert window.start <= window.end
            assert window.start.year == window.end.year == year
    assert seasonal_window("unknown", 2026) is None
