"""キャラの今の状態（C5 / C6）: 境界の時刻・返答の指針・次の予定・直近の出来事。"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import pytest

from app.engine.calendar.generator import plan_range
from app.engine.calendar.life import DefaultSpec, life_spec_from_persona
from app.engine.calendar.models import EventView
from app.engine.calendar.state import (
    build_snapshot,
    day_label,
    describe_event,
    next_event_text,
    recent_event_texts,
    reply_style_hint,
)
from app.engine.types import JST
from tests.engine.calendar.personas import office_persona

DEFAULT = DefaultSpec(activity="家でのんびり", location="自宅", status_label="のんびり中", busyness=0)


def jst(month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=JST)


def event(
    title: str,
    start: datetime,
    end: datetime,
    *,
    kind: str = "routine",
    busyness: int = 1,
    label: str | None = None,
    location: str | None = None,
    mood: str | None = None,
    status: str = "scheduled",
) -> EventView:
    return EventView(
        id=uuid.uuid4(),
        kind=kind,
        title=title,
        starts_at=start,
        ends_at=end,
        busyness=busyness,
        source="generator",
        source_key=f"{kind}:{title}",
        generated_for=start.date(),
        location=location,
        mood=mood,
        status=status,
        meta={"status_label": label} if label else {},
    )


SLEEP = event("睡眠", jst(9, 25, 0), jst(9, 25, 7), busyness=3, label="おやすみ中")
WORK = event("仕事", jst(9, 25, 9, 30), jst(9, 25, 18, 30), busyness=2, label="仕事中", location="オフィス")
DRINKS = event(
    "同期と飲み会",
    jst(9, 25, 19, 30),
    jst(9, 25, 23),
    kind="oneoff",
    busyness=2,
    label="飲み会中",
    location="新宿の居酒屋",
    mood="ほろ酔いで上機嫌",
)
NEXT_SLEEP = event("睡眠", jst(9, 26, 0, 30), jst(9, 26, 9, 30), busyness=3, label="おやすみ中")
EVENTS = [SLEEP, WORK, DRINKS, NEXT_SLEEP]


def test_state_during_an_event() -> None:
    snap = build_snapshot(EVENTS, DEFAULT, jst(9, 25, 21, 30))
    assert snap.activity == "同期と飲み会"
    assert snap.location == "新宿の居酒屋"
    assert snap.mood == "ほろ酔いで上機嫌"
    assert snap.busyness == 2
    assert snap.status_label == "飲み会中"
    assert snap.event_id == DRINKS.id
    assert snap.event_kind == "oneoff"
    assert "短め" in snap.reply_style_hint
    assert snap.next_event is not None
    assert "23:00ごろまで" in snap.next_event
    # 睡眠は「次の予定」に出さない
    assert "睡眠" not in snap.next_event


def test_state_boundaries_are_half_open() -> None:
    # 開始の瞬間は含み、終了の瞬間は含まない（[start, end)）
    assert build_snapshot(EVENTS, DEFAULT, jst(9, 25, 19, 30)).event_id == DRINKS.id
    at_end = build_snapshot(EVENTS, DEFAULT, jst(9, 25, 23))
    assert at_end.event_id is None
    assert at_end.activity == "家でのんびり"
    assert at_end.status_label == "のんびり中"
    assert at_end.busyness == 0
    assert build_snapshot(EVENTS, DEFAULT, jst(9, 25, 18, 29)).event_id == WORK.id


def test_state_in_a_gap_uses_default_activity() -> None:
    snap = build_snapshot(EVENTS, DEFAULT, jst(9, 25, 7, 30))
    assert snap.event_id is None
    assert snap.activity == "家でのんびり"
    assert snap.location == "自宅"
    assert "余裕" in snap.reply_style_hint
    assert snap.next_event is not None
    assert snap.next_event.startswith("次は今日 9:30〜18:30 仕事")


def test_sleeping_state_hint() -> None:
    snap = build_snapshot(EVENTS, DEFAULT, jst(9, 25, 3))
    assert snap.busyness == 3
    assert snap.status_label == "おやすみ中"
    assert "眠そう" in snap.reply_style_hint


def test_recent_events_within_48_hours() -> None:
    old = event("旅行", jst(9, 20, 9), jst(9, 20, 19), kind="oneoff", location="鎌倉")
    snap = build_snapshot([*EVENTS, old], DEFAULT, jst(9, 26, 10))
    assert any(r == "昨日 19:30〜23:00 同期と飲み会（新宿の居酒屋）" for r in snap.recent_events)
    assert all("旅行" not in r for r in snap.recent_events)
    # 49 時間後には出ない
    later = recent_event_texts([*EVENTS, old], jst(9, 27, 23, 1))
    assert all("同期と飲み会" not in r for r in later)


def test_recent_includes_just_finished_routine_but_not_sleep() -> None:
    texts = recent_event_texts(EVENTS, jst(9, 25, 19))
    assert texts == ("今日 9:30〜18:30 仕事（オフィス）",)
    assert recent_event_texts(EVENTS, jst(9, 25, 8)) == ()  # 睡眠は出さない


def test_cancelled_events_are_ignored() -> None:
    cancelled = event("キャンセルした予定", jst(9, 25, 7), jst(9, 25, 9), kind="oneoff", status="cancelled")
    snap = build_snapshot([cancelled], DEFAULT, jst(9, 25, 8))
    assert snap.event_id is None


@pytest.mark.parametrize(
    ("offset", "label"),
    [(-2, "おととい"), (-1, "昨日"), (0, "今日"), (1, "明日"), (2, "あさって"), (3, "9月29日（火）")],
)
def test_day_labels(offset: int, label: str) -> None:
    today = date(2026, 9, 26)
    assert day_label(today + timedelta(days=offset), today) == label


def test_describe_cross_midnight_event() -> None:
    karaoke = event("カラオケ", jst(9, 25, 22), jst(9, 26, 2), kind="oneoff")
    assert describe_event(karaoke, jst(9, 26, 10)) == "昨日 22:00〜翌2:00 カラオケ"


def test_reply_hints_never_push_purchases() -> None:
    hints = [reply_style_hint(b, "仕事", "仕事中") for b in range(4)] + [reply_style_hint(3, "睡眠", "おやすみ中")]
    for hint in hints:
        for word in ("課金", "購入", "有料", "買って", "トークン", "プレミアム"):  # scope-check: allow（E2 の検査語）
            assert word not in hint
    assert "1文" in reply_style_hint(3, "ライブ", "ライブ中")


def test_next_event_text_without_upcoming() -> None:
    assert next_event_text([], jst(9, 25, 12), None) is None


def test_snapshot_over_a_generated_week_is_always_defined() -> None:
    spec = life_spec_from_persona(office_persona())
    events = [EventView.from_planned(e) for e in plan_range(spec, uuid.uuid4(), date(2026, 9, 20), date(2026, 9, 28))]
    now = jst(9, 21, 0)
    while now < jst(9, 28, 0):
        snap = build_snapshot(events, spec.default, now)
        assert snap.activity
        assert 0 <= snap.busyness <= 3
        assert snap.status_label
        now += timedelta(minutes=37)
