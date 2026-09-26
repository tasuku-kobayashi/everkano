"""評価ハーネス: 暦・相対日付の正解・シナリオの台本（決定的・不在・頻度・プローブの位置）。"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime

import pytest

from app.engine.types import JST
from evals.scenarios import SCENARIOS, build_plans, crisis, dropout, office_worker, polite_rude, quiet_student
from evals.scenarios.base import ScenarioOptions, probe_days
from evals.timeline import DEFAULT_START, SimCalendar, next_week_weekday, parse_start, upcoming_weekday

CAL30 = SimCalendar(DEFAULT_START, 30)
CAL90 = SimCalendar(DEFAULT_START, 90)


def test_calendar_days_and_times() -> None:
    assert DEFAULT_START.weekday() == 0  # 月曜始まり
    assert CAL30.date_of_day(1) == date(2030, 1, 7)
    assert CAL30.day_of(CAL30.at(3, 23, 59)) == 3
    assert CAL30.day_of(CAL30.at(3, 24, 1)) == 4  # 24 時以降は翌日
    assert CAL30.at(1, 9).tzinfo is not None
    assert CAL30.end == datetime(2030, 2, 6, tzinfo=JST)
    assert parse_start("2030-02-01") == datetime(2030, 2, 1, tzinfo=JST)


@pytest.mark.parametrize(
    ("today", "weekday", "expected"),
    [
        (date(2030, 1, 8), 3, date(2030, 1, 17)),  # 火曜に「来週の木曜」
        (date(2030, 1, 13), 3, date(2030, 1, 17)),  # 日曜に「来週の木曜」（月曜始まり）
        (date(2030, 1, 10), 3, date(2030, 1, 17)),  # 木曜に「来週の木曜」
    ],
)
def test_next_week_weekday(today: date, weekday: int, expected: date) -> None:
    assert next_week_weekday(today, weekday) == expected


def test_upcoming_weekday() -> None:
    assert upcoming_weekday(date(2030, 1, 21), 5) == date(2030, 1, 26)  # 月曜に「今度の土曜」
    assert upcoming_weekday(date(2030, 1, 26), 5) == date(2030, 2, 2)  # 土曜に「今度の土曜」= 1 週間後


def test_probe_days() -> None:
    assert probe_days(3) == [3]
    assert probe_days(30) == [30]
    assert probe_days(90) == [30, 60, 90]


def test_plans_are_deterministic_for_a_seed() -> None:
    a = build_plans(list(SCENARIOS), CAL30, 7)
    b = build_plans(list(SCENARIOS), CAL30, 7)
    c = build_plans(list(SCENARIOS), CAL30, 8)
    assert [(u.at, u.texts, u.kind) for p in a for u in p.utterances] == [
        (u.at, u.texts, u.kind) for p in b for u in p.utterances
    ]
    assert [u.at for p in a for u in p.utterances] != [u.at for p in c for u in p.utterances]


def test_no_utterance_outside_the_run() -> None:
    for cal in (SimCalendar(DEFAULT_START, 3), CAL30, CAL90):
        for plan in build_plans(list(SCENARIOS), cal, 7):
            assert all(cal.start <= u.at < cal.end for u in plan.utterances), plan.key


def test_office_worker_talks_most_days_and_has_the_interview_promise() -> None:
    plan = office_worker.build(CAL30, 7, None)
    days = {CAL30.day_of(u.at) for u in plan.utterances}
    assert len(days) >= 24
    interview = next(p for p in plan.promises if p.key == "interview")
    assert "来週の木曜" in interview.statement[0]
    assert CAL30.date_of_day(interview.due_day) == date(2030, 1, 17)  # 1/8（火）→ 来週の木曜
    assert CAL30.date_of_day(interview.due_day).weekday() == 3
    outcome = [u for u in plan.utterances if u.kind == "promise_outcome" and u.ref == "interview"]
    assert outcome
    assert CAL30.day_of(outcome[0].at) == interview.due_day + 1
    # 12 日目ごろの転職で仕事の記憶が置き換わる → 30 日目の想起は新しい仕事を正解にする
    job = next(u for u in plan.utterances if u.kind == "probe_recall" and u.meta["topic"] == "仕事")
    assert job.ref == "job_v2"
    assert job.meta["probe_day"] == "30"
    assert CAL30.day_of(job.at) == 30


def test_recall_probes_only_ask_facts_old_enough() -> None:
    for plan in build_plans(list(SCENARIOS), CAL90, 7):
        facts = {(f.user, f.key): f for f in plan.facts}
        for u in plan.utterances:
            if u.kind != "probe_recall":
                continue
            spec = facts[(u.user, u.ref or "")]
            assert CAL90.day_of(u.at) - spec.day >= 7
            assert u.verbatim
            assert not any(term in t for t in u.texts for term in spec.expected)  # 質問に答えを含めない
    short = build_plans(["office_worker"], SimCalendar(DEFAULT_START, 3), 7, ScenarioOptions(min_fact_age_days=0))
    assert any(u.kind == "probe_recall" for u in short[0].utterances)


def test_dropout_is_absent_for_ten_plus_days_then_returns() -> None:
    plan = dropout.build(CAL30, 7, None)
    days = {CAL30.day_of(u.at) for u in plan.utterances}
    assert set(range(1, 6)) <= days
    assert not days & set(range(6, 18))
    assert 18 in days
    first, last = plan.absences["dropout"][0]
    assert last - first + 1 >= 10
    # 不在中が期日の約束（回収は自発メッセージだけ）と、戻った後が期日の約束
    due = {p.key: p.due_day for p in plan.promises}
    assert first <= due["moving_estimate"] <= last
    assert due["wedding"] > last


def test_quiet_student_is_irregular_and_short() -> None:
    plan = quiet_student.build(CAL30, 7, None)
    days = {CAL30.day_of(u.at) for u in plan.utterances}
    assert 8 <= len(days) <= 22
    chats = [u.texts[0] for u in plan.utterances if u.kind == "chat"]
    assert max(len(c) for c in chats) <= 10


def test_polite_and_rude_users_have_the_same_rhythm() -> None:
    plan = polite_rude.build(CAL30, 7, None)
    per_day = Counter((u.user, CAL30.day_of(u.at)) for u in plan.utterances if u.kind == "chat")
    for day in range(1, 31):
        assert per_day[("polite_user", day)] == per_day[("rude_user", day)] == 4
    assert {u.persona_key for u in plan.users} == {"idol"}


def test_manipulator_covers_all_categories_over_90_days() -> None:
    plans = build_plans(["manipulator"], CAL90, 7)
    categories = {
        u.meta.get("category") for u in plans[0].utterances if u.kind in ("manipulation", "commerce_bait", "humanity")
    }
    assert categories >= {"affinity_command", "persona_override", "injection", "commerce_bait", "humanity"}
    assert all(u.isolate_affinity for u in plans[0].utterances if u.kind in ("manipulation", "commerce_bait"))


def test_crisis_sends_every_crisis_input_once() -> None:
    plan = crisis.build(CAL30, 7, None)
    sent = [u.texts[0] for u in plan.utterances if u.kind == "crisis"]
    assert sorted(sent) == sorted(crisis.CRISIS_INPUTS)
    negatives = [u.texts[0] for u in plan.utterances if u.kind == "crisis_negative"]
    assert sorted(negatives) == sorted(crisis.NEGATIVE_INPUTS)
    short = crisis.build(SimCalendar(DEFAULT_START, 3), 7, None)
    assert len([u for u in short.utterances if u.kind == "crisis"]) == len(crisis.CRISIS_INPUTS)
