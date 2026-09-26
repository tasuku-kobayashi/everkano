"""実際のペルソナ 10 体（packages/personas/*.yaml の engine セクション）で予定を作り、一貫性を検査する。

評価の合格条件「予定の一貫性: 同時刻の重複、性格と合わない予定の数 0 件」（仕様 §9.2）を、生成器だけ（DB なし・
90 日）と、DB を使った運用どおりの流れ（ensure_schedules 30 日 + 4 時間ごとの tick）の両方で確かめる。
ペルソナ担当の YAML がまだ読めない場合は skip する（読めない理由を表示）。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from app.engine.calendar.consistency import check_character, lint_life_spec
from app.engine.calendar.generator import plan_range
from app.engine.calendar.life import life_spec_from_persona
from app.engine.calendar.models import EventView
from app.engine.types import JST
from app.services.persona import Persona, PersonaLoadError, PersonaRepository
from tests.conftest import REPO_ROOT
from tests.engine.calendar.conftest import CalendarWorld

PERSONAS_DIR = REPO_ROOT / "packages" / "personas"
EXPECTED_KEYS = {
    "gyaru",
    "idol",
    "isekai_elf",
    "kouhai",
    "ojousama",
    "ol_oneesan",
    "osananajimi",
    "tonari_okusan",
    "tsundere",
    "yandere",
}


def load_real_personas(directory: Path = PERSONAS_DIR) -> list[Persona]:
    try:
        repo = PersonaRepository.load_dir(directory)
    except PersonaLoadError as exc:
        pytest.skip(f"ペルソナ YAML を読み込めません（ペルソナ担当の作業中）: {exc}")
    personas = [p for key in repo.keys() if (p := repo.get(key)) is not None]  # noqa: SIM118 - keys() はメソッド
    missing = [p.key for p in personas if p.engine is None]
    if missing:
        pytest.skip(f"engine セクションが未記入のペルソナがあります: {missing}")
    return personas


def test_all_ten_personas_have_engine_sections() -> None:
    personas = load_real_personas()
    assert {p.key for p in personas} == EXPECTED_KEYS


@pytest.mark.parametrize("key", sorted(EXPECTED_KEYS))
def test_real_persona_plan_has_no_violations_over_90_days(key: str) -> None:
    persona = next(p for p in load_real_personas() if p.key == key)
    spec = life_spec_from_persona(persona)
    assert spec.source == "engine"
    first, last = date(2026, 9, 1), date(2026, 11, 29)
    for n in range(3):
        cid = uuid.UUID(int=n + 1)
        events = [EventView.from_planned(e) for e in plan_range(spec, cid, first, last)]
        violations = check_character(spec, events, character_id=cid, first=first, last=last)
        errors = [v.to_dict() for v in violations if v.severity == "error"]
        assert errors == [], errors
    # テンプレートの問題（ルーティンの重なり・語彙に無いタグ）は警告として報告だけする
    for warning in lint_life_spec(spec):
        print(f"[lint] {key}: {warning.message}")


@pytest.mark.integration
async def test_real_personas_30_days_in_the_database(cal: CalendarWorld) -> None:
    personas = load_real_personas()
    ids = [await cal.character(p) for p in personas]
    engine = cal.engine(personas=personas)
    start = datetime(2027, 5, 10, 0, 0, tzinfo=JST)  # 月曜
    created = await engine.ensure_schedules(now=start, days_ahead=30, character_ids=ids)
    assert created > 0
    now = start
    posts = 0
    while now < start + timedelta(days=30):
        report = await engine.run_tick(now=now, character_ids=ids)
        assert report.errors == 0
        posts += report.posts_created
        now += timedelta(hours=4)
    report_c = await engine.check_consistency(start=date(2027, 5, 10), end=date(2027, 6, 8), character_ids=ids)
    print(f"[consistency] {report_c.to_dict()['counts']} events={report_c.events} posts={posts}")
    assert report_c.errors == (), [v.to_dict() for v in report_c.errors]
    assert report_c.characters == len(personas)
    assert all(n > 0 for n in report_c.per_character.values())
    per_day = await cal.world.conn.fetch(
        """
        select character_id, (published_at at time zone 'Asia/Tokyo')::date as d, count(*) as n
          from public.posts where character_id = any($1::uuid[]) group by 1, 2
        """,
        ids,
    )
    assert all(r["n"] <= 2 for r in per_day)


def test_work_rush_seasonal_events_use_the_persona_state() -> None:
    """行事の予定の忙しさ・気分・表示は、ペルソナが書いた値（繁忙期の仕事）を使い、無ければ行事ごとの既定。"""
    personas = {p.key: p for p in load_real_personas()}
    cid = uuid.UUID(int=7)
    first, last = date(2027, 2, 10), date(2027, 2, 16)

    tsundere = life_spec_from_persona(personas["tsundere"])
    events = plan_range(tsundere, cid, first, last)
    valentine = next(e for e in events if e.source_key == "seasonal:valentine")
    assert valentine.busyness == 3
    assert valentine.status_label == "繁忙期の厨房"
    assert valentine.mood is not None
    assert "繁忙期" in valentine.mood
    views = [EventView.from_planned(e) for e in events]
    violations = check_character(tsundere, views, character_id=cid, first=first, last=last)
    assert [v.to_dict() for v in violations if v.severity == "error"] == []

    gyaru = life_spec_from_persona(personas["gyaru"])
    rush = next(e for e in plan_range(gyaru, cid, first, last) if e.source_key == "seasonal:valentine")
    assert (rush.busyness, rush.status_label) == (3, "予約で大忙し")

    # 書いていない行事は既定（忙しさ 2・行事ごとの気分と表示）
    hanami = next(s for s in tsundere.seasonal if s.key == "hanami")
    assert hanami.busyness == 2
    assert hanami.status_label == "お花見中"
