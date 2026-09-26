"""相対的な日付表現の解決（日本時間・週は月曜始まり）。固定の時計で、週・月・年の境目を確かめる。"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from app.engine.memory.dates import date_reference, due_at, resolve_future_date, resolve_past_date
from app.engine.types import JST


def jst(y: int, m: int, d: int, hh: int = 12, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=JST)


# 2026-09-26 は土曜、2026-09-27 は日曜、2026-09-30 は水曜、2026-12-30 は水曜
@pytest.mark.parametrize(
    ("now", "text", "expected", "precision"),
    [
        # 来週 = 次の月曜〜日曜
        (jst(2026, 9, 26), "来週の木曜、面接なんだよね", date(2026, 10, 1), "day"),
        (jst(2026, 9, 27), "来週の木曜、面接", date(2026, 10, 1), "day"),  # 日曜 → 翌日からが来週
        (jst(2026, 9, 27), "来週の日曜", date(2026, 10, 4), "day"),
        (jst(2026, 9, 28), "来週の月曜", date(2026, 10, 5), "day"),  # 月曜 → 7 日後
        (jst(2026, 9, 30), "来週木曜日に出張", date(2026, 10, 8), "day"),
        (jst(2026, 9, 30), "再来週の火曜", date(2026, 10, 13), "day"),
        (jst(2026, 9, 30), "今週の金曜", date(2026, 10, 2), "day"),
        # 今度の / 曜日だけ = 今日より後の最初のその曜日
        (jst(2026, 9, 26), "今度の土曜に映画", date(2026, 10, 3), "day"),
        (jst(2026, 9, 26), "土曜に映画見よう", date(2026, 10, 3), "day"),
        (jst(2026, 9, 26), "月曜は会議", date(2026, 9, 28), "day"),
        # 明日・明後日（深夜 0 時台の日付の繰り上がり）
        (jst(2026, 9, 30), "明日は歯医者", date(2026, 10, 1), "day"),
        (datetime(2026, 9, 30, 15, 30, tzinfo=UTC), "明日は歯医者", date(2026, 10, 2), "day"),  # JST 10/1 0:30
        (jst(2026, 9, 30), "あさってライブ", date(2026, 10, 2), "day"),
        (jst(2026, 12, 31), "明日は初詣", date(2027, 1, 1), "day"),
        # 週末
        (jst(2026, 9, 30), "週末は実家に帰る", date(2026, 10, 3), "day"),
        (jst(2026, 9, 26), "週末ひま？", date(2026, 9, 26), "day"),  # 土曜なら今日
        (jst(2026, 9, 27), "週末に旅行", date(2026, 10, 3), "day"),  # 日曜なら次の週末
        (jst(2026, 9, 30), "来週末は旅行", date(2026, 10, 10), "day"),
        # 日付
        (jst(2026, 9, 26), "10月3日にライブ", date(2026, 10, 3), "day"),
        (jst(2026, 9, 26), "１０／５に行く", date(2026, 10, 5), "day"),
        (jst(2026, 12, 30), "1月3日に帰省", date(2027, 1, 3), "day"),  # 過ぎた日付は来年
        (jst(2026, 9, 26), "9月25日の話", date(2026, 9, 25), "day"),  # 7 日以内の過去はそのまま（約束にはしない）
        (jst(2026, 9, 26), "2027年2月14日", date(2027, 2, 14), "day"),
        (jst(2026, 9, 26), "来月の5日", date(2026, 10, 5), "day"),
        (jst(2026, 12, 20), "来月10日に", date(2027, 1, 10), "day"),
        (jst(2026, 9, 26), "25日に給料日", date(2026, 10, 25), "day"),
        # 月・週だけ
        (jst(2026, 9, 26), "来月は忙しい", date(2026, 10, 1), "month"),
        (jst(2026, 12, 15), "来月引っ越す", date(2027, 1, 1), "month"),
        (jst(2026, 9, 30), "来週から出張", date(2026, 10, 5), "week"),
        (jst(2026, 9, 30), "月末締め", date(2026, 9, 30), "day"),
        # 〜後
        (jst(2026, 9, 30), "3日後に試験", date(2026, 10, 3), "day"),
        (jst(2026, 9, 30), "2週間後に旅行", date(2026, 10, 14), "day"),
        (jst(2026, 1, 31), "1ヶ月後に発表", date(2026, 2, 28), "month"),
    ],
)
def test_resolve_future_date(now: datetime, text: str, expected: date, precision: str) -> None:
    resolved = resolve_future_date(text, now)
    assert resolved is not None, text
    assert resolved.date == expected, text
    assert resolved.precision == precision, text


def test_time_makes_datetime_precision() -> None:
    now = jst(2026, 9, 26)
    resolved = resolve_future_date("明日の夜7時に待ち合わせ", now)
    assert resolved is not None
    assert resolved.precision == "datetime"
    assert resolved.time == time(19, 0)
    resolved = resolve_future_date("明日15:30から会議", now)
    assert resolved is not None
    assert resolved.time == time(15, 30)
    resolved = resolve_future_date("来週の月曜 午後2時半に面談", now)
    assert resolved is not None
    assert (resolved.date, resolved.time) == (date(2026, 9, 28), time(14, 30))
    # 「2時間」は時刻ではない
    assert resolve_future_date("明日は2時間くらい走る", now).precision == "day"  # type: ignore[union-attr]


@pytest.mark.parametrize("text", ["今度教えて", "2時間待った", "いつかね", "毎週日曜はジム"])
def test_no_date(text: str) -> None:
    assert resolve_future_date(text, jst(2026, 9, 26)) is None


def test_resolve_past_date() -> None:
    now = jst(2026, 10, 1, 9)
    assert resolve_past_date("昨日カフェに行った", now) == (date(2026, 9, 30), "昨日")
    assert resolve_past_date("おととい映画を観た", now) == (date(2026, 9, 29), "おととい")
    assert resolve_past_date("今朝は早起きした", now) == (date(2026, 10, 1), "今朝")
    assert resolve_past_date("先月の旅行", now) is None
    assert resolve_past_date("楽しかった", now) is None


def test_due_at_uses_noon_jst_for_day_precision() -> None:
    assert due_at(date(2026, 10, 1), None, "day") == datetime(2026, 10, 1, 3, 0, tzinfo=UTC)
    assert due_at(date(2026, 10, 1), time(19, 0), "datetime") == datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    assert due_at(date(2026, 10, 1), time(19, 0), "day") == datetime(2026, 10, 1, 3, 0, tzinfo=UTC)
    assert due_at(None, None, "unknown") is None
    assert due_at(date(2026, 10, 1), None, "unknown") is None


def test_date_reference_lists_weeks_monday_first() -> None:
    text = date_reference(jst(2026, 9, 27))  # 日曜
    assert "今日: 2026-09-27(日)" in text
    assert "来週: 2026-09-28(月)〜2026-10-04(日)" in text
    assert "再来週: 2026-10-05(月)〜2026-10-11(日)" in text
