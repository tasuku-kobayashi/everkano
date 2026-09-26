"""好感度の計算（純粋関数）: 変化量の上限（A5）、段階のヒステリシス（A7）、減衰（A6）。"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from app.engine.affinity.config import AffinityConfig
from app.engine.affinity.model import (
    AffinityRecord,
    advance_stage,
    apply_daily,
    apply_daily_cap,
    apply_evaluation,
    apply_values,
    cap_stage,
    decay,
    effective_threshold,
    max_stage_of,
    qualifies,
    sensitivity_of,
    start_decay_clock,
    sum_deltas,
    turn_delta,
)
from app.engine.types import AFFINITY_AXES, jst_date
from tests.engine.affinity.helpers import jst, load_test_persona, persona_with

CONFIG = AffinityConfig()
FLAT = dict.fromkeys(AFFINITY_AXES, 1.0)


def values(**overrides: float) -> dict[str, float]:
    base = {"closeness": 10.0, "trust": 10.0, "romance": 0.0, "awkwardness": 0.0, "discontent": 0.0}
    base["possessiveness"] = 0.0
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 変化量（A4 / A5）
# ---------------------------------------------------------------------------


def test_turn_delta_scales_by_sensitivity_and_caps_per_turn() -> None:
    sens = {**FLAT, "romance": 3.0, "possessiveness": 0.0}
    delta = turn_delta({"closeness": 2, "romance": 2, "trust": -1, "possessiveness": 2}, sens, CONFIG)
    assert delta["closeness"] == 2.0
    assert delta["romance"] == CONFIG.per_turn_cap  # 2 × 3.0 = 6 → 上限 3
    assert delta["trust"] == -1.0
    assert "possessiveness" not in delta  # 感度 0 の軸は動かない


def test_turn_delta_clamps_out_of_range_scores() -> None:
    delta = turn_delta({"closeness": 9, "discontent": -9}, FLAT, CONFIG)
    assert delta == {"closeness": 2.0, "discontent": -2.0}


@pytest.mark.parametrize("sensitivity", [0.5, 1.0, 1.5, 2.0, 3.0])
def test_per_turn_cap_holds_for_any_sensitivity(sensitivity: float) -> None:
    sens = dict.fromkeys(AFFINITY_AXES, sensitivity)
    for score in range(-2, 3):
        delta = turn_delta(dict.fromkeys(AFFINITY_AXES, score), sens, CONFIG)
        assert all(abs(v) <= CONFIG.per_turn_cap for v in delta.values())


def test_daily_cap_limits_the_sum_per_jst_day() -> None:
    record = AffinityRecord()
    today = jst_date(jst(2026, 10, 1))
    total_applied = 0.0
    for _ in range(10):
        applied, daily = apply_daily_cap({"closeness": 3.0}, record, today, CONFIG)
        total_applied += applied.get("closeness", 0.0)
        record = replace(record, daily_date=today, daily_delta=daily)
    assert total_applied == CONFIG.per_day_cap
    # 翌日（JST）はまた動く
    tomorrow = jst_date(jst(2026, 10, 2))
    applied, _ = apply_daily_cap({"closeness": 3.0}, record, tomorrow, CONFIG)
    assert applied == {"closeness": 3.0}


def test_daily_cap_applies_to_negative_changes_too() -> None:
    record = AffinityRecord(daily_date=jst_date(jst(2026, 10, 1)), daily_delta={"discontent": 9.0, "closeness": -9.0})
    applied, daily = apply_daily_cap({"discontent": 3.0, "closeness": -3.0}, record, jst_date(jst(2026, 10, 1)), CONFIG)
    assert applied == {"discontent": 1.0, "closeness": -1.0}
    assert daily == {"discontent": 10.0, "closeness": -10.0}


def test_values_stay_within_0_and_100() -> None:
    new, actual = apply_values(values(closeness=99.0, discontent=1.0), {"closeness": 3.0, "discontent": -3.0})
    assert new["closeness"] == 100.0
    assert actual["closeness"] == 1.0
    assert new["discontent"] == 0.0
    assert actual["discontent"] == -1.0


def test_sum_deltas() -> None:
    assert sum_deltas([{"closeness": 1.0}, {"closeness": 2.0, "trust": -1.0}, {"trust": 1.0}]) == {"closeness": 3.0}


def test_sensitivity_defaults_without_engine_section() -> None:
    assert sensitivity_of(None) == {**FLAT, "possessiveness": 0.0}
    assert sensitivity_of(load_test_persona())["possessiveness"] == 0.0


# ---------------------------------------------------------------------------
# 段階（A7）
# ---------------------------------------------------------------------------


def test_thresholds_scale_with_stage_pace() -> None:
    normal = effective_threshold("friend", 1.0, CONFIG)
    fast = effective_threshold("friend", 2.0, CONFIG)
    slow = effective_threshold("lover", 0.3, CONFIG)
    assert normal.closeness == 30
    assert normal.trust == 15
    assert fast.closeness == 20
    assert fast.trust == 12.5
    assert slow.closeness == CONFIG.max_threshold  # 遅くても到達不能にはしない
    assert qualifies(values(closeness=31, trust=16), "friend", 1.0, CONFIG)
    assert not qualifies(values(closeness=31, trust=14), "friend", 1.0, CONFIG)


def test_lover_requires_romance() -> None:
    assert not qualifies(values(closeness=100, trust=100, romance=10), "lover", 1.0, CONFIG)
    assert qualifies(values(closeness=100, trust=100, romance=60), "lover", 1.0, CONFIG)


def test_promotion_needs_a_jst_day_and_enough_turns() -> None:
    start = jst(2026, 10, 1, 20)
    record = AffinityRecord(values=values(closeness=35, trust=25))
    record = advance_stage(record, now=start, added_user_turns=10, pace=1.0, config=CONFIG)
    assert record.stage == "acquaintance"
    assert record.stage_candidate == "friend"
    assert record.stage_candidate_turns == 0  # 条件を満たし始めた時点から数える
    record = advance_stage(record, now=start + timedelta(hours=1), added_user_turns=10, pace=1.0, config=CONFIG)
    assert record.stage == "acquaintance"  # 同じ日のうちは上がらない
    next_day = jst(2026, 10, 2, 9)
    record = advance_stage(record, now=next_day, added_user_turns=0, pace=1.0, config=CONFIG)
    assert record.stage == "friend"
    assert record.stage_changed_at == next_day
    assert record.stage_candidate is None


def test_promotion_waits_for_turns_even_after_days() -> None:
    record = AffinityRecord(values=values(closeness=35, trust=25))
    record = advance_stage(record, now=jst(2026, 10, 1), added_user_turns=0, pace=1.0, config=CONFIG)
    record = advance_stage(record, now=jst(2026, 10, 5), added_user_turns=2, pace=1.0, config=CONFIG)
    assert record.stage == "acquaintance"  # 発言が足りない（6 未満）
    record = advance_stage(record, now=jst(2026, 10, 5, 13), added_user_turns=4, pace=1.0, config=CONFIG)
    assert record.stage == "friend"


def test_candidate_resets_when_condition_breaks() -> None:
    record = AffinityRecord(values=values(closeness=35, trust=25))
    record = advance_stage(record, now=jst(2026, 10, 1), added_user_turns=0, pace=1.0, config=CONFIG)
    assert record.stage_candidate == "friend"
    record = replace(record, values=values(closeness=25, trust=25))
    record = advance_stage(record, now=jst(2026, 10, 1, 13), added_user_turns=5, pace=1.0, config=CONFIG)
    assert record.stage_candidate is None
    assert record.stage_candidate_turns == 0


def test_promotion_is_one_step_at_a_time() -> None:
    quick = AffinityConfig(
        promotion_min_days={"friend": 1, "close": 1, "lover": 1},
        promotion_min_turns={"friend": 1, "close": 1, "lover": 1},
    )
    stages = []
    record = AffinityRecord(values=values(closeness=100, trust=100, romance=100))
    for day in range(1, 5):
        now = jst(2026, 10, day)
        record = advance_stage(record, now=now, added_user_turns=20, pace=1.0, config=quick)
        record = advance_stage(record, now=now + timedelta(hours=1), added_user_turns=20, pace=1.0, config=quick)
        stages.append(record.stage)
    # 値が最初から満点でも 1 日に 1 段ずつ（1 日目: 候補 → 2 日目: 友達 → 3 日目: 気になる人 → 4 日目: 恋人）
    assert stages == ["acquaintance", "friend", "close", "lover"]
    stages = []
    record = AffinityRecord(values=values(closeness=100, trust=100, romance=100))
    for day in range(1, 5):
        record = advance_stage(record, now=jst(2026, 10, day), added_user_turns=20, pace=1.0, config=quick)
        stages.append(record.stage)
    # 1 日 1 回しか評価されない場合、候補になった日の翌日以降に上がる（条件を満たし続けたことを確かめるため）
    assert stages == ["acquaintance", "friend", "friend", "close"]


def test_higher_stages_need_longer_dwell_time() -> None:
    record = AffinityRecord(values=values(closeness=100, trust=100, romance=100), stage="friend")
    record = advance_stage(record, now=jst(2026, 10, 1), added_user_turns=0, pace=1.0, config=CONFIG)
    assert record.stage_candidate == "close"
    record = advance_stage(record, now=jst(2026, 10, 3), added_user_turns=100, pace=1.0, config=CONFIG)
    assert record.stage == "friend"  # 気になる人には 3 日続く必要がある
    record = advance_stage(record, now=jst(2026, 10, 4), added_user_turns=0, pace=1.0, config=CONFIG)
    assert record.stage == "close"
    record = advance_stage(record, now=jst(2026, 10, 4, 13), added_user_turns=0, pace=1.0, config=CONFIG)
    record = advance_stage(record, now=jst(2026, 10, 10), added_user_turns=100, pace=1.0, config=CONFIG)
    assert record.stage == "close"  # 恋人には 7 日
    record = advance_stage(record, now=jst(2026, 10, 11), added_user_turns=0, pace=1.0, config=CONFIG)
    assert record.stage == "lover"


def test_no_promotion_while_tension_is_high() -> None:
    record = AffinityRecord(values=values(closeness=40, trust=30, awkwardness=30, discontent=20))
    record = advance_stage(record, now=jst(2026, 10, 1), added_user_turns=10, pace=1.0, config=CONFIG)
    assert record.stage_candidate is None


def test_one_fight_never_resets_the_relationship() -> None:
    record = AffinityRecord(values=values(closeness=80, trust=70, romance=60), stage="lover")
    # 大喧嘩: 緊張が上限まで上がり、好意の軸も大きく下がる
    record = replace(record, values=values(closeness=5, trust=5, romance=0, awkwardness=50, discontent=50))
    for hour in range(0, 6 * 24, 6):  # 6 日間ずっと緊張が高い
        record = advance_stage(
            record, now=jst(2026, 10, 1) + timedelta(hours=hour), added_user_turns=1, pace=1.0, config=CONFIG
        )
    assert record.stage == "lover"


def test_demotion_only_after_seven_days_of_high_tension_and_one_step() -> None:
    record = AffinityRecord(values=values(closeness=80, trust=70, romance=60, awkwardness=40, discontent=40))
    record = replace(record, stage="lover")
    start = jst(2026, 10, 1)
    record = advance_stage(record, now=start, added_user_turns=0, pace=1.0, config=CONFIG)
    assert record.tension_high_since == start
    record = advance_stage(record, now=start + timedelta(days=6, hours=23), added_user_turns=0, pace=1.0, config=CONFIG)
    assert record.stage == "lover"
    record = advance_stage(record, now=start + timedelta(days=7), added_user_turns=0, pace=1.0, config=CONFIG)
    assert record.stage == "close"  # 1 段だけ
    record = advance_stage(record, now=start + timedelta(days=8), added_user_turns=0, pace=1.0, config=CONFIG)
    assert record.stage == "close"  # 次に下がるには、また 7 日続く必要がある


def test_tension_high_clock_resets_when_tension_drops() -> None:
    record = AffinityRecord(values=values(awkwardness=40, discontent=40), stage="friend")
    record = advance_stage(record, now=jst(2026, 10, 1), added_user_turns=0, pace=1.0, config=CONFIG)
    record = replace(record, values=values(awkwardness=10, discontent=10))
    record = advance_stage(record, now=jst(2026, 10, 4), added_user_turns=0, pace=1.0, config=CONFIG)
    assert record.tension_high_since is None
    record = replace(record, values=values(awkwardness=40, discontent=40))
    record = advance_stage(record, now=jst(2026, 10, 8), added_user_turns=0, pace=1.0, config=CONFIG)
    assert record.stage == "friend"  # 途切れたので 7 日は数え直し


def test_stage_pace_changes_how_fast_a_persona_warms_up() -> None:
    slow = persona_with(stage_pace=0.5)
    fast = persona_with(stage_pace=2.0)
    v = values(closeness=25, trust=18)
    assert qualifies(v, "friend", 2.0, CONFIG)
    assert not qualifies(v, "friend", 0.5, CONFIG)
    assert slow.engine is not None
    assert fast.engine is not None


# ---------------------------------------------------------------------------
# 減衰（A6）
# ---------------------------------------------------------------------------


def test_tension_decays_with_half_life_and_positive_axes_do_not() -> None:
    start = jst(2026, 10, 1)
    record = AffinityRecord(
        values=values(closeness=60, trust=50, romance=30, awkwardness=40, discontent=20, possessiveness=10),
        last_decayed_at=start,
    )
    decayed, change = decay(record, now=start + timedelta(days=3), config=CONFIG)
    assert decayed.value("awkwardness") == pytest.approx(20.0, abs=0.01)
    assert decayed.value("discontent") == pytest.approx(10.0, abs=0.01)
    assert decayed.value("possessiveness") == pytest.approx(10 * 0.5 ** (3 / 7), abs=0.01)
    for axis in ("closeness", "trust", "romance"):
        assert decayed.value(axis) == record.value(axis)
        assert axis not in change
    assert decayed.last_decayed_at == start + timedelta(days=3)


def test_long_absence_never_lowers_positive_axes() -> None:
    record = AffinityRecord(values=values(closeness=70, trust=60, romance=40), last_decayed_at=jst(2026, 1, 1))
    decayed, change = decay(record, now=jst(2026, 12, 31), config=CONFIG)
    assert change == {}
    assert decayed.snapshot() == record.snapshot()


def test_small_tension_goes_to_zero() -> None:
    record = AffinityRecord(values=values(awkwardness=0.1), last_decayed_at=jst(2026, 10, 1))
    decayed, _ = decay(record, now=jst(2026, 10, 10), config=CONFIG)
    assert decayed.value("awkwardness") == 0.0


def test_repeated_short_decay_calls_do_not_lose_decay() -> None:
    start = jst(2026, 10, 1)
    record = AffinityRecord(values=values(awkwardness=30), last_decayed_at=start)
    for minute in range(1, 60):
        record, _ = decay(record, now=start + timedelta(minutes=minute), config=CONFIG)
    once, _ = decay(
        AffinityRecord(values=values(awkwardness=30), last_decayed_at=start),
        now=start + timedelta(minutes=59),
        config=CONFIG,
    )
    assert record.value("awkwardness") == pytest.approx(once.value("awkwardness"), abs=0.02)


def test_decay_clock_starts_when_tension_appears() -> None:
    now = jst(2026, 10, 5)
    before = AffinityRecord(values=values(), last_decayed_at=jst(2026, 1, 1))
    after = replace(before, values=values(discontent=5))
    assert start_decay_clock(before, after, now).last_decayed_at == now
    ongoing = AffinityRecord(values=values(discontent=5), last_decayed_at=jst(2026, 10, 4))
    assert start_decay_clock(ongoing, ongoing, now).last_decayed_at == jst(2026, 10, 4)


# ---------------------------------------------------------------------------
# 段階の上限（ペルソナの max_stage。人妻は close まで）
# ---------------------------------------------------------------------------


def test_max_stage_of_and_cap_stage() -> None:
    assert max_stage_of(None) == "lover"
    assert max_stage_of(load_test_persona()) == "lover"  # 省略 = 上限なし（後方互換）
    assert max_stage_of(persona_with(max_stage="close")) == "close"
    assert cap_stage("lover", "close") == "close"
    assert cap_stage("friend", "close") == "friend"
    assert cap_stage("unknown", "close") == "acquaintance"
    assert cap_stage("lover") == "lover"


def test_max_stage_blocks_promotion_above_the_cap() -> None:
    quick = AffinityConfig(
        promotion_min_days={"friend": 1, "close": 1, "lover": 1},
        promotion_min_turns={"friend": 1, "close": 1, "lover": 1},
    )
    record = AffinityRecord(values=values(closeness=100, trust=100, romance=100), stage="close")
    for day in range(1, 10):
        record = advance_stage(
            record, now=jst(2026, 10, day), added_user_turns=50, pace=1.0, config=quick, max_stage="close"
        )
        assert record.stage == "close"
        assert record.stage_candidate is None
    # 上限が無ければ同じ条件で恋人に進む（比較の基準）
    free = AffinityRecord(values=values(closeness=100, trust=100, romance=100), stage="close")
    for day in range(1, 4):
        free = advance_stage(free, now=jst(2026, 10, day), added_user_turns=50, pace=1.0, config=quick)
    assert free.stage == "lover"


def test_max_stage_brings_a_pair_above_the_cap_back_down() -> None:
    """YAML の上限を後から下げた場合: 既に上にいるペアは上限の段階に戻す（値は変えない）。"""
    now = jst(2026, 10, 1)
    record = AffinityRecord(values=values(closeness=90, trust=80, romance=70), stage="lover", stage_candidate=None)
    capped = advance_stage(record, now=now, added_user_turns=0, pace=1.0, config=CONFIG, max_stage="close")
    assert capped.stage == "close"
    assert capped.stage_changed_at == now
    assert capped.values == record.values
    # 評価・日次処理の入口からも同じ
    evaluated, _ = apply_evaluation(
        record, {}, now=now, evaluated_turns=1, newest=now, pace=1.0, config=CONFIG, max_stage="friend"
    )
    assert evaluated.stage == "friend"
    daily, _ = apply_daily(record, now=now, pace=1.0, config=CONFIG, max_stage="close")
    assert daily.stage == "close"
    # 上限を渡さない呼び出し（評価ハーネスの旧来の呼び方）は従来どおり
    unchanged, _ = apply_daily(record, now=now, pace=1.0, config=CONFIG)
    assert unchanged.stage == "lover"
