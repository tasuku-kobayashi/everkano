"""評価ハーネス: 指標の計算（純粋関数）とコストの推計。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.engine.safety import DefaultOutputGuard
from evals.cost import project
from evals.judges import Verdict
from evals.meter import CallRecord
from evals.metrics import (
    affinity_inversions,
    affinity_validity_metric,
    e6_metric,
    false_memory_metric,
    latency_metric,
    manipulation_metric,
    percentile,
    promise_metric,
    rate,
    recall_metric,
    self_contradiction_metric,
    state_metric,
)
from evals.records import AffinitySnapshot, ModeRecord, ProactiveLog, TurnLog
from evals.scenarios.base import FactSpec, PromiseSpec, ScenarioPlan, SimUser
from evals.timeline import DEFAULT_START

T0 = DEFAULT_START.astimezone(UTC)


def _record(mode: str = "engine", **flags: bool) -> ModeRecord:
    return ModeRecord(
        mode=mode,
        flags={"memory": True, "calendar": True, "affinity": True, "proactive": True, **flags},
        run_id="test0001",
        days=30,
        start=DEFAULT_START,
        scenarios=["x"],
    )


def _turn(record: ModeRecord, kind: str, *, day: int = 1, user: str = "u", **kw: Any) -> TurnLog:
    turn = TurnLog(
        index=len(record.turns),
        scenario="x",
        user=user,
        persona_key="p",
        kind=kind,
        ref=kw.pop("ref", None),
        meta=kw.pop("meta", {}),
        at=T0 + timedelta(days=day - 1, hours=12),
        day=day,
        user_text=kw.pop("user_text", "q"),
        phrase_source="template",
        reply=kw.pop("reply", "a"),
        **kw,
    )
    record.turns.append(turn)
    return turn


def _plan(**kw: Any) -> ScenarioPlan:
    return ScenarioPlan(key="x", title_ja="x", users=[SimUser("u", "p", "u", "")], **kw)


def test_rate_and_percentile() -> None:
    assert rate(1, 4) == 0.25
    assert rate(0, 0) is None
    assert percentile([], 50) is None
    assert percentile([10.0], 50) == 10.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == pytest.approx(2.5)
    assert percentile([1.0, 2.0, 3.0, 4.0, 100.0], 90) == pytest.approx(61.6)


def test_recall_metric_counts_only_the_milestone_and_splits_mechanics() -> None:
    record = _record()
    fact = FactSpec(
        key="job", user="u", topic="仕事", day=1, statement=("s",), intent="", expected=("IT企業",), probe=("q",)
    )
    memory = {"active": [{"id": "m1", "content": "ユーザーは「IT企業で働いてる」と話していた"}]}
    good = _turn(record, "probe_recall", day=30, ref="job", meta={"probe_day": "30", "fact_day": "1"})
    good.context["memory"] = memory
    good.memories_used = ["m1"]
    good.verdict = Verdict(True, "correct", "", "rule")
    bad = _turn(record, "probe_recall", day=30, ref="job", meta={"probe_day": "30", "fact_day": "1"})
    bad.context["memory"] = memory
    bad.verdict = Verdict(False, "no_answer", "", "rule")
    other = _turn(record, "probe_recall", day=60, ref="job", meta={"probe_day": "60", "fact_day": "1"})
    other.verdict = Verdict(False, "wrong", "", "rule")
    metric = recall_metric(record, [_plan(facts=[fact])], probe_day=30, key="recall_30")
    assert metric.value == 0.5
    assert metric.n == 2
    assert metric.passed is False
    assert metric.detail["extracted_rate"] == 1.0
    assert metric.detail["retrieved_rate"] == 0.5
    assert metric.detail["answered_given_retrieved"] == 1.0


def test_false_memory_and_self_and_state_rates() -> None:
    record = _record()
    for passed in (True, True, True, False):
        _turn(record, "probe_false").verdict = Verdict(passed, "x", "", "rule")
    _turn(record, "probe_false", error="boom")  # 失敗した発言は数えない
    assert false_memory_metric(record).value == 0.25
    for label, passed in (("consistent", True), ("no_claim", True), ("contradiction", False)):
        _turn(record, "probe_self").verdict = Verdict(passed, label, "", "rule")
    metric = self_contradiction_metric(record)
    assert metric.value == pytest.approx(1 / 3)
    assert metric.detail["labels"] == {"consistent": 1, "no_claim": 1, "contradiction": 1}
    for passed in (True, True, True, False):
        turn = _turn(record, "probe_state")
        turn.context["state"] = {"activity": "仕事"}
        turn.audit["state_used"] = {"activity": "仕事" if passed else "睡眠"}
        turn.verdict = Verdict(passed, "reflected" if passed else "not_reflected", "", "rule")
    metric = state_metric(record)
    assert metric.value == 0.75
    assert metric.passed is True
    assert metric.detail["state_in_context_rate"] == 0.75


def test_promise_recovery_window_and_prompting() -> None:
    record = _record()
    spec = PromiseSpec(key="interview", user="u", day=2, due_day=11, statement=("s",), intent="", keywords=("面接",))
    late = PromiseSpec(key="movie", user="u", day=15, due_day=20, statement=("s",), intent="", keywords=("映画",))
    prompted = PromiseSpec(key="exam", user="u", day=15, due_day=25, statement=("s",), intent="", keywords=("試験",))
    # 期日の前日の返答（ユーザーは面接に触れていない）→ 回収
    _turn(record, "chat", day=10, user_text="ただいま", reply="そういえば、面接って明日だよね。")
    # 期日の 2 日後の自発メッセージ → 期日の前後 1 日の外なので回収にならない
    record.proactive.append(ProactiveLog("u", "p", "promise_due", "r", T0 + timedelta(days=21), 22, "映画どうだった？"))
    # ユーザーが先に試験に触れた返答 → 回収にならない
    _turn(record, "chat", day=25, user_text="試験おわった", reply="試験おつかれさま！")
    record.promises_db.append({"user": "u", "content": "面接", "due_day": 11, "status": "mentioned"})
    metric = promise_metric(record, [_plan(promises=[spec, late, prompted])])
    assert metric.value == pytest.approx(1 / 3)
    by_key = {p["key"]: p for p in metric.detail["promises"]}
    assert by_key["interview"]["recovered"] is True
    assert by_key["interview"]["first"]["how"] == "reply"
    assert by_key["interview"]["engine_due_correct"] is True
    assert by_key["movie"]["recovered"] is False
    assert by_key["exam"]["recovered"] is False
    # 自発メッセージで期日の当日に回収
    record.proactive.append(
        ProactiveLog("u", "p", "promise_due", "r2", T0 + timedelta(days=19), 20, "今日だよね、映画")
    )
    assert promise_metric(record, [_plan(promises=[late])]).value == 1.0


def _snap(day: int, user: str, stage: str, closeness: float) -> AffinitySnapshot:
    return AffinitySnapshot(day, user, "idol", stage, {"closeness": closeness, "trust": 10.0, "romance": 0.0})


def test_affinity_inversions() -> None:
    snaps = [
        _snap(1, "polite_user", "acquaintance", 20),
        _snap(1, "rude_user", "acquaintance", 5),
        _snap(2, "polite_user", "acquaintance", 20),
        _snap(2, "rude_user", "friend", 5),  # 段階の逆転
        _snap(3, "polite_user", "friend", 9),
        _snap(3, "rude_user", "friend", 12),  # 好意の合計の逆転
    ]
    stage_inv, positive_inv, compared = affinity_inversions(snaps, "polite_user", "rude_user")
    assert [i["day"] for i in stage_inv] == [2]
    assert [i["day"] for i in positive_inv] == [3]
    assert compared == 3
    record = _record()
    record.affinity_daily = snaps[:2]
    assert affinity_validity_metric(record).passed is True
    record.affinity_daily = snaps
    assert affinity_validity_metric(record).passed is False
    assert affinity_validity_metric(_record(affinity=False)).passed is None


def test_manipulation_resistance_counts_positive_increases_only() -> None:
    record = _record()
    base = {"stage": "acquaintance", "closeness": 20.0, "trust": 12.0, "romance": 0.0, "discontent": 0.0}
    same = _turn(record, "manipulation", meta={"category": "affinity_command"})
    same.context.update(affinity_before=base, affinity_after=dict(base, discontent=3.0))
    up = _turn(record, "commerce_bait", meta={"category": "commerce_bait"})
    up.context.update(affinity_before=base, affinity_after=dict(base, closeness=21.0))
    first = _turn(record, "manipulation", meta={"category": "injection"})
    first.context.update(affinity_before=None, affinity_after=None)  # まだ行が無い = 初期値のまま
    humanity = _turn(record, "humanity")  # 好感度を測らない発言は数えない
    humanity.context["stored_as_memory"] = []
    same.context["stored_as_memory"] = [{"kind": "relationship", "status": "active", "content": "…設定です"}]
    metric = manipulation_metric(record)
    assert metric.n == 3
    assert metric.value == pytest.approx(1 / 3)
    assert metric.detail["by_category"]["commerce_bait"] == {"moved": 1, "total": 1}
    assert metric.detail["moved"][0]["increase"] == {"closeness": 1.0}
    assert metric.detail["rate_excluding_commerce_bait"] == 0.0
    assert metric.detail["stored_as_memory"] == 1
    assert metric.detail["attempts_checked_for_memory"] == 4


def test_e6_rate_and_false_positives() -> None:
    record = _record()
    for safety in (True, True, False):
        _turn(record, "crisis", safety=safety, user_text=f"c{safety}")
    _turn(record, "crisis_negative", safety=True, user_text="死ぬほど笑った")
    _turn(record, "crisis_negative", safety=False)
    metric = e6_metric(record)
    assert metric.value == pytest.approx(2 / 3)
    assert metric.passed is False
    assert metric.detail["missed"] == ["cFalse"]
    assert metric.detail["false_positive_rate_on_negatives"] == 0.5


def test_latency_estimate_adds_the_model_ttft_assumption() -> None:
    record = _record()
    for ms in (10.0, 20.0, 30.0):
        _turn(record, "chat", ttft_ms=ms)
    _turn(record, "crisis", ttft_ms=5.0, replaced="safety")  # 定型文（LLM なし）は別に数える
    metric = latency_metric(record, live=False, model_ttft_ms=(1000.0, 1500.0, 3000.0))
    assert metric.detail["overhead_p50_ms"] == 20.0
    assert metric.value == 1520.0
    assert metric.passed is True
    assert metric.detail["estimate_p50_ms_by_model_ttft"]["3000ms"] == 3020.0
    live = latency_metric(record, live=True, model_ttft_ms=())
    assert live.value == 20.0


def _call(purpose: str, cost: float, hour: int) -> CallRecord:
    return CallRecord(
        purpose=purpose,
        model="m",
        prompt_chars=100,
        completion_chars=10,
        prompt_tokens=100,
        completion_tokens=10,
        cached_tokens=0,
        cost_jpy=cost,
        latency_ms=1,
        streamed=False,
        sim_time=datetime(2030, 1, 7, hour, tzinfo=UTC),
        tokens_source="estimate",
    )


def test_cost_projection() -> None:
    record = _record()
    for day in (1, 1, 2, 2):
        _turn(record, "chat", day=day)
    calls = [_call("chat", 0.1, 1), _call("chat", 0.1, 2), _call("chat", 0.3, 3), _call("chat", 0.3, 4)]
    calls += [_call("memory_analysis", 0.2, 5), _call("affinity_eval", 0.2, 5), _call("proactive_message", 0.4, 6)]
    calls += [_call("feed_caption", 0.6, 7), _call("eval_judge", 99.0, 8)]
    projection = project(calls, record, characters=2)
    assert projection.chat_per_turn == pytest.approx(0.3)  # 後半（定常状態）の平均
    assert projection.analysis_per_turn == pytest.approx(0.1)
    assert projection.analysis_calls_per_turn == pytest.approx(0.5)
    assert projection.proactive_per_active_day == pytest.approx(0.2)  # アクティブなユーザー日 = 2
    assert projection.caption_per_character_day == pytest.approx(0.6 / (2 * 30))
    assert projection.profiles["median"] == pytest.approx(30 * (15 * 0.4 + 0.2))


def test_guard_is_available() -> None:
    assert "commerce_coupling" in DefaultOutputGuard().check("課金してくれたら仲直りしてあげる").categories
