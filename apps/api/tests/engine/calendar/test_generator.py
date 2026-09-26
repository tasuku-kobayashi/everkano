"""予定の生成（C2〜C4・C10・C11）: 決定性・重なりなし・日またぎ・置き換え・抽選の条件・フォールバック。"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import date, datetime, timedelta
from itertools import pairwise

import pytest

from app.engine.calendar.consistency import check_character, check_overlaps, lint_life_spec
from app.engine.calendar.fallback import fallback_life_spec
from app.engine.calendar.generator import (
    BIRTHDAY_SOURCE_KEY,
    PlanContext,
    PlannedEvent,
    clip_against_existing,
    plan_day,
    plan_range,
    resolve,
    subtract_intervals,
)
from app.engine.calendar.life import is_sleep_like, life_spec_from_persona, span_minutes
from app.engine.calendar.models import EventView
from app.engine.types import JST
from app.services.persona import Persona
from app.services.types import CharacterRecord
from tests.engine.calendar.personas import all_fixture_personas, early_persona, night_persona, office_persona

START = date(2026, 9, 1)
DAYS_90 = START + timedelta(days=89)


def jst(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=JST)


def views(events: list[PlannedEvent]) -> list[EventView]:
    return [EventView.from_planned(e) for e in events]


def signature(events: list[PlannedEvent]) -> list[tuple[str, str, datetime, datetime]]:
    return [(e.kind, e.source_key, e.starts_at, e.ends_at) for e in events]


@pytest.mark.parametrize("persona", all_fixture_personas(), ids=lambda p: p.key)
def test_generation_is_deterministic(persona: Persona) -> None:
    spec = life_spec_from_persona(persona)
    cid = uuid.uuid4()
    first = plan_range(spec, cid, START, START + timedelta(days=27))
    # 別のインスタンス（ペルソナを読み直しても）・日ごとに生成しても同じ結果
    spec2 = life_spec_from_persona(persona.model_copy(deep=True))
    daily: list[PlannedEvent] = []
    for i in range(28):
        daily.extend(plan_day(spec2, cid, START + timedelta(days=i)))
    assert signature(first) == signature(daily)


def test_oneoffs_depend_on_the_character() -> None:
    spec = life_spec_from_persona(office_persona())
    a = [e for e in plan_range(spec, uuid.UUID(int=1), START, DAYS_90) if e.kind == "oneoff"]
    b = [e for e in plan_range(spec, uuid.UUID(int=2), START, DAYS_90) if e.kind == "oneoff"]
    assert signature(a) != signature(b)


@pytest.mark.parametrize("persona", all_fixture_personas(), ids=lambda p: p.key)
def test_no_overlaps_and_no_violations_over_90_days(persona: Persona) -> None:
    spec = life_spec_from_persona(persona)
    cid = uuid.uuid4()
    events = plan_range(spec, cid, START, DAYS_90)
    assert events
    assert check_overlaps(views(events)) == []
    violations = check_character(spec, views(events), character_id=cid, first=START, last=DAYS_90)
    assert [v for v in violations if v.severity == "error"] == []
    # 生成の元はペルソナのテンプレートだけ（C11: 性格と合わない予定を作らない）
    keys = {r.key for r in spec.routine} | {t.source_key for t in spec.oneoffs} | {s.source_key for s in spec.seasonal}
    assert {e.source_key for e in events} <= keys | {BIRTHDAY_SOURCE_KEY}


def test_cross_midnight_sleep_blocks() -> None:
    spec = life_spec_from_persona(early_persona())
    cid = uuid.uuid4()
    thursday = date(2026, 9, 24)
    events = plan_day(spec, cid, thursday)
    sleep = [e for e in events if is_sleep_like(e.title)]
    assert len(sleep) == 1
    # 木曜の 21:30 〜 金曜 5:00（木曜の予定として生成）
    assert sleep[0].starts_at == jst(2026, 9, 24, 21, 30)
    assert sleep[0].ends_at == jst(2026, 9, 25, 5, 0)
    assert sleep[0].generated_for == thursday


def test_24h_notation_starts_on_the_next_day() -> None:
    spec = life_spec_from_persona(office_persona())
    events = plan_day(spec, uuid.uuid4(), date(2026, 9, 27))  # 日曜
    sleep = [e for e in events if is_sleep_like(e.title)]
    # 日曜の "24:00"〜"07:00" = 月曜 0:00〜7:00（日曜の予定）
    assert [(s.starts_at, s.ends_at) for s in sleep] == [(jst(2026, 9, 28, 0), jst(2026, 9, 28, 7))]
    assert span_minutes("24:30", "09:30") == (1470, 1440 + 570)


def _find_day(spec_persona: Persona, predicate: object, days: int = 180) -> tuple[uuid.UUID, date, list[PlannedEvent]]:
    spec = life_spec_from_persona(spec_persona)
    for seed in range(50):
        cid = uuid.UUID(int=1000 + seed)
        for offset in range(days):
            day = START + timedelta(days=offset)
            events = plan_day(spec, cid, day)
            if callable(predicate) and predicate(events):
                return cid, day, events
    pytest.fail("条件に合う日が見つかりません")


def test_oneoff_replaces_routine_on_the_same_day() -> None:
    # 土曜の日帰り旅行（9:30〜19:00）は、週末のカフェ（13:00〜16:00）を置き換える
    _cid, _day, events = _find_day(office_persona(), lambda es: any(e.source_key == "event:weekend_trip" for e in es))
    trip = next(e for e in events if e.source_key == "event:weekend_trip")
    assert not any(e.title == "カフェで読書" for e in events)
    assert all(not e.overlaps(trip.starts_at, trip.ends_at) for e in events if e is not trip)


def test_oneoff_splits_routine_across_midnight() -> None:
    # 月曜の朝市（4:00〜5:30）が、日曜の夜の睡眠（21:30〜翌5:00）の終わりを切り取る
    spec = life_spec_from_persona(early_persona())
    cid, day, events = _find_day(early_persona(), lambda es: any(e.source_key == "event:market" for e in es))
    market = next(e for e in events if e.source_key == "event:market")
    previous = plan_day(spec, cid, day - timedelta(days=1))
    sleep = [e for e in previous if is_sleep_like(e.title)]
    assert sleep
    assert sleep[-1].ends_at == market.starts_at  # 5:00 → 4:00 に短くなる
    assert sleep[-1].segment == 0


def test_cross_midnight_oneoff_clips_evening_routine() -> None:
    # 月・火のカラオケオール（22:00〜翌2:00）が、ゲーム（20:00〜24:30）を 22:00 までに切り取る
    _cid, _day, events = _find_day(night_persona(), lambda es: any(e.source_key == "event:karaoke_night" for e in es))
    karaoke = next(e for e in events if e.source_key == "event:karaoke_night")
    game = [e for e in events if e.title == "ゲーム"]
    assert karaoke.ends_at - karaoke.starts_at == timedelta(hours=4)
    assert [(g.starts_at.hour, g.ends_at) for g in game] == [(20, karaoke.starts_at)]


def test_min_interval_days_is_respected() -> None:
    spec = life_spec_from_persona(early_persona())
    for n in range(10):
        events = plan_range(spec, uuid.UUID(int=n), START, START + timedelta(days=364))
        days = sorted(e.generated_for for e in events if e.source_key == "event:hot_spring")
        assert len(days) >= 5  # 週 0.9 の確率だが 21 日空ける
        assert all((b - a).days >= 21 for a, b in pairwise(days))


def test_months_restriction() -> None:
    spec = life_spec_from_persona(early_persona())
    events = plan_range(spec, uuid.uuid4(), date(2026, 1, 1), date(2026, 12, 31))
    walks = [e for e in events if e.source_key == "event:sakura_walk"]
    assert walks
    assert {e.generated_for.month for e in walks} <= {3, 4}
    office = life_spec_from_persona(office_persona())
    lives = [
        e
        for n in range(5)
        for e in plan_range(office, uuid.UUID(int=n), date(2026, 1, 1), date(2026, 12, 31))
        if e.source_key == "event:night_live"
    ]
    assert lives
    assert {e.generated_for.month for e in lives} <= {6, 7, 12}


def test_weekly_probability_is_roughly_respected() -> None:
    spec = life_spec_from_persona(office_persona())
    counts = Counter()
    weeks = 0
    for n in range(20):
        events = plan_range(spec, uuid.UUID(int=n), date(2026, 1, 5), date(2026, 12, 27))  # 51 週
        counts.update(e.source_key for e in events if e.kind == "oneoff")
        weeks += 51
    # 金曜の飲み会は週 0.6（同じ日のライブに負けることがある）、残業は週 0.5
    assert 0.45 < counts["event:friday_drinks"] / weeks < 0.65
    assert 0.4 < counts["event:overtime"] / weeks < 0.6
    assert counts["event:catch_cold"] / weeks < 0.1


def test_seasonal_events_are_placed_inside_their_window() -> None:
    spec = life_spec_from_persona(office_persona())
    cid = uuid.uuid4()
    events = plan_range(spec, cid, date(2026, 1, 1), date(2026, 12, 31))
    seasonal = {e.source_key: e for e in events if e.kind == "seasonal"}
    assert set(seasonal) == {
        "seasonal:hanami",
        "seasonal:summer_festival",
        "seasonal:tsukimi",
        "seasonal:christmas",
        "seasonal:new_year",
    }
    hanami = seasonal["seasonal:hanami"]
    assert date(2026, 3, 25) <= hanami.generated_for <= date(2026, 4, 10)
    assert hanami.generated_for.weekday() >= 5  # 休日を選ぶ
    assert hanami.title == "お花見"
    assert hanami.description == "hanamiが好き"  # ペルソナの reaction（行事への気持ち）
    assert seasonal["seasonal:tsukimi"].generated_for == date(2026, 9, 25)
    assert seasonal["seasonal:tsukimi"].title == "お月見"  # タイトルの既定値
    assert seasonal["seasonal:christmas"].generated_for in (date(2026, 12, 24), date(2026, 12, 25))
    assert all(e.notable for e in seasonal.values())


def test_seasonal_day_avoids_work_for_weekend_workers() -> None:
    # 木〜月に働く早番のパン職人の紅葉狩り（9:00〜15:00）は、休みの火・水に入る
    spec = life_spec_from_persona(early_persona())
    for n in range(10):
        events = plan_range(spec, uuid.UUID(int=n), date(2026, 11, 1), date(2026, 12, 31))
        leaves = [e for e in events if e.source_key == "seasonal:autumn_leaves"]
        assert len(leaves) == 1
        assert leaves[0].generated_for.weekday() in (1, 2)


def test_birthday_event_and_leap_day() -> None:
    office = life_spec_from_persona(office_persona())
    events = plan_day(office, uuid.uuid4(), date(2026, 10, 2))
    birthday = [e for e in events if e.source_key == BIRTHDAY_SOURCE_KEY]
    assert len(birthday) == 1
    assert birthday[0].starts_at == jst(2026, 10, 2, 19)
    night = life_spec_from_persona(night_persona())
    assert any(e.source_key == BIRTHDAY_SOURCE_KEY for e in plan_day(night, uuid.uuid4(), date(2027, 2, 28)))
    assert any(e.source_key == BIRTHDAY_SOURCE_KEY for e in plan_day(night, uuid.uuid4(), date(2028, 2, 29)))
    assert not any(e.source_key == BIRTHDAY_SOURCE_KEY for e in plan_day(night, uuid.uuid4(), date(2028, 2, 28)))


def test_weekday_worker_is_off_on_public_holidays() -> None:
    spec = life_spec_from_persona(office_persona())
    assert spec.holiday_as_sunday
    holiday = plan_day(spec, uuid.uuid4(), date(2026, 9, 21))  # 敬老の日（月）
    assert not any(e.title == "仕事" for e in holiday)
    assert any(e.title == "カフェで読書" for e in holiday)
    workday = plan_day(spec, uuid.uuid4(), date(2026, 9, 24))
    assert any(e.title == "仕事" for e in workday)
    # 土日に働く人は祝日でも曜日どおり
    night = life_spec_from_persona(night_persona())
    assert not night.holiday_as_sunday


def test_participants_for_templates_naming_another_character() -> None:
    spec = life_spec_from_persona(office_persona())
    yoru_id = uuid.uuid4()
    context = PlanContext(companions={"ヨル": yoru_id})
    events = [
        e
        for n in range(10)
        for e in plan_range(spec, uuid.UUID(int=n), START, START + timedelta(days=60), context)
        if e.source_key == "event:lunch_with_yoru"
    ]
    assert events
    assert all(e.participants == (yoru_id,) for e in events)
    others = [
        e for e in plan_range(spec, uuid.UUID(int=1), START, START + timedelta(days=60), context) if e.kind != "oneoff"
    ]
    assert all(e.participants == () for e in others)


def test_resolve_priorities() -> None:
    day = date(2026, 9, 26)

    def ev(kind: str, key: str, start: int, end: int) -> PlannedEvent:
        return PlannedEvent(
            kind=kind,  # type: ignore[arg-type]
            source="seasonal" if kind == "seasonal" else "generator",
            source_key=key,
            title=key,
            starts_at=jst(2026, 9, 26, start),
            ends_at=jst(2026, 9, 26, end),
            generated_for=day,
            busyness=1,
            status_label=key,
        )

    placed = resolve(
        [
            ev("routine", "routine:a", 8, 20),
            ev("oneoff", "event:b", 10, 12),
            ev("oneoff", "event:c", 11, 13),  # b と重なる → 落ちる
            ev("seasonal", "seasonal:d", 15, 16),
        ]
    )
    assert [(e.source_key, e.starts_at.hour, e.ends_at.hour) for e in placed] == [
        ("routine:a", 8, 10),
        ("event:b", 10, 12),
        ("routine:a", 12, 15),
        ("seasonal:d", 15, 16),
        ("routine:a", 16, 20),
    ]
    assert [e.segment for e in placed if e.kind == "routine"] == [0, 1, 2]


def test_subtract_intervals() -> None:
    a, b = jst(2026, 1, 1, 8), jst(2026, 1, 1, 20)
    pieces = subtract_intervals(a, b, [(jst(2026, 1, 1, 10), jst(2026, 1, 1, 12)), (jst(2026, 1, 1, 7), a)])
    assert pieces == [(a, jst(2026, 1, 1, 10)), (jst(2026, 1, 1, 12), b)]
    assert subtract_intervals(a, b, [(a, b)]) == []


def test_clip_against_existing_events() -> None:
    spec = life_spec_from_persona(office_persona())
    day = date(2026, 9, 24)  # 木曜
    planned = plan_day(spec, uuid.uuid4(), day)
    work = next(e for e in planned if e.title == "仕事")
    existing = [(jst(2026, 9, 24, 12), jst(2026, 9, 24, 13))]
    kept, dropped = clip_against_existing(planned, existing)
    pieces = [e for e in kept if e.title == "仕事"]
    assert [(p.starts_at, p.ends_at) for p in pieces] == [
        (work.starts_at, existing[0][0]),
        (existing[0][1], work.ends_at),
    ]
    assert dropped == []
    # 単発は重なれば落とす
    oneoff = PlannedEvent(
        kind="oneoff",
        source="generator",
        source_key="event:x",
        title="x",
        starts_at=jst(2026, 9, 24, 12, 30),
        ends_at=jst(2026, 9, 24, 14),
        generated_for=day,
        busyness=1,
        status_label="x",
    )
    kept, dropped = clip_against_existing([oneoff], existing)
    assert kept == []
    assert dropped == [oneoff]
    assert check_overlaps(views(clip_against_existing(planned, existing)[0])) == []


def _fallback(schedule: str) -> Persona:
    record = CharacterRecord(
        id=uuid.uuid4(),
        handle="fb",
        name="フォールバック",
        avatar_url="",
        bio=None,
        persona_key="nokey",
        system_prompt="",
        is_active=True,
    )
    return Persona.fallback(record).model_copy(update={"schedule_pattern": schedule})


def test_fallback_without_schedule_is_sleep_only() -> None:
    spec = life_spec_from_persona(_fallback(""))
    assert spec.source == "default"
    assert [(r.start, r.end, r.busyness) for r in spec.routine] == [("00:30", "07:30", 3)]
    assert spec.default.activity == "のんびり過ごしている"
    events = plan_range(spec, uuid.uuid4(), START, DAYS_90)
    assert len(events) == 90
    assert check_overlaps(views(events)) == []


def test_fallback_from_schedule_pattern() -> None:
    spec = fallback_life_spec(
        _fallback(
            "平日: 7:00起床 / 8:15 駅前のカフェでコーヒー / 9:00出社 / 12:30 同僚とランチ"
            " / 19:00-21:00退社（帰りに一杯のことも） / 22:00以降はお風呂と読書でリラックス / 1:00就寝\n"
            "休日: 昼まで寝る / 午後はカフェ・本屋めぐり"
        )
    )
    assert spec.source == "schedule_pattern"
    blocks = {(r.activity, r.start, r.end, tuple(sorted(r.days))) for r in spec.routine}
    weekdays = tuple(sorted(["mon", "tue", "wed", "thu", "fri"]))
    assert ("寝ている（睡眠）", "01:00", "07:00", weekdays) in blocks
    assert ("仕事", "09:00", "19:00", weekdays) in blocks
    assert ("寝ている（睡眠）", "02:00", "11:30", ("sat", "sun")) in blocks
    assert spec.holiday_as_sunday
    events = plan_range(spec, uuid.uuid4(), START, DAYS_90)
    assert check_overlaps(views(events)) == []


def test_fallback_with_explicit_days_and_work_ranges() -> None:
    spec = fallback_life_spec(
        _fallback(
            "平日（木〜月）: 5:30起床 / 7:00出勤 / 10:00-16:00 展示・ふれあいガイド / 17:30退勤 / 22:00就寝\n"
            "休日（火曜・水曜）: 昼まで二度寝"
        )
    )
    days = {r.activity: r.days for r in spec.routine}
    assert days["仕事"] == frozenset({"thu", "fri", "sat", "sun", "mon"})
    sleep = [r for r in spec.routine if r.is_sleep]
    assert {(r.start, r.end) for r in sleep} == {("22:00", "05:30"), ("02:00", "11:30")}
    assert not spec.holiday_as_sunday
    events = plan_range(spec, uuid.uuid4(), START, DAYS_90)
    assert check_overlaps(views(events)) == []


def test_lint_reports_overlapping_routines_and_unknown_tags() -> None:
    persona = office_persona()
    assert lint_life_spec(life_spec_from_persona(persona)) == []
    data = persona.model_dump()
    data["engine"]["life"]["routine"].append(
        {
            "days": ["mon"],
            "start": "09:00",
            "end": "10:00",
            "activity": "朝のミーティング",
            "location": "オフィス",
            "busyness": 2,
            "status_label": "会議中",
            "post_tags": ["meeting_room"],
        }
    )
    broken = Persona.model_validate(data)
    codes = Counter(v.code for v in lint_life_spec(life_spec_from_persona(broken)))
    assert codes["routine_overlap"] >= 1
    assert codes["unknown_tag"] == 1
