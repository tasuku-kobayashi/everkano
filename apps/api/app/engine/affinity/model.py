"""好感度の計算（純粋関数。DB・LLM に依存しない）。

- 変化量: 離散評価（-2〜+2）× points_per_step × ペルソナの感度 → 1 ターンの上限 → 1 日（JST）の上限（A5）
- 段階: 閾値（stage_pace でスケール）+ ヒステリシス（A7）+ ペルソナの上限（max_stage。人妻は close まで）
  - 昇格: 次の段階の条件を「続けて」満たし、条件を満たし始めた JST の日から promotion_min_days 日以上たち、
    その間のユーザーの発言が promotion_min_turns 以上のときに 1 段だけ上がる。緊張が高いあいだは上がらない。
  - 降格: 緊張（気まずさ + 不満）が demotion_tension 以上の状態が demotion_days 日続いたときだけ 1 段だけ下がる。
    点数が閾値を下回っただけでは下がらない（一度の喧嘩で他人に戻らない）。
- 減衰（A6）: 好意の軸（親しさ・信頼・ときめき）は減衰しない。緊張の軸は半減期 tension_half_life_days で 0 に向かう。
  独占欲もゆっくり落ち着く。会わない期間で値が下がる・罰が生じることはない。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Final

from app.engine.affinity.config import INITIAL_VALUES, AffinityConfig, StageThreshold
from app.engine.types import AFFINITY_AXES, STAGES, TENSION_AXES, jst_date
from app.services.persona import AffinitySensitivity, Persona

MIN_SCORE: Final[int] = -2
MAX_SCORE: Final[int] = 2
VALUE_MIN: Final[float] = 0.0
VALUE_MAX: Final[float] = 100.0
_SECONDS_PER_DAY: Final[float] = 86400.0


def _round(value: float) -> float:
    return round(value, 2)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True, slots=True)
class AffinityRecord:
    """affinity_states の 1 行（ユーザー × キャラ）。"""

    values: Mapping[str, float] = field(default_factory=lambda: dict(INITIAL_VALUES))
    stage: str = "acquaintance"
    stage_changed_at: datetime | None = None
    stage_candidate: str | None = None
    stage_candidate_since: datetime | None = None
    stage_candidate_turns: int = 0
    tension_high_since: datetime | None = None
    last_interaction_at: datetime | None = None
    daily_date: date | None = None
    daily_delta: Mapping[str, float] = field(default_factory=dict)
    evaluated_until: datetime | None = None
    user_turns: int = 0
    last_decayed_at: datetime | None = None
    absence_days: int | None = None
    absence_return_at: datetime | None = None

    def value(self, axis: str) -> float:
        return float(self.values.get(axis, INITIAL_VALUES.get(axis, 0.0)))

    @property
    def tension(self) -> float:
        return sum(self.value(axis) for axis in TENSION_AXES)

    def snapshot(self) -> dict[str, float]:
        """履歴（affinity_history.before / after）に書く軸の値。"""
        return {axis: _round(self.value(axis)) for axis in AFFINITY_AXES}


# ---------------------------------------------------------------------------
# ペルソナの感度（A3）
# ---------------------------------------------------------------------------

DEFAULT_SENSITIVITY: Final = AffinitySensitivity()


def sensitivity_of(persona: Persona | None) -> dict[str, float]:
    sens = persona.engine.affinity.sensitivity if persona is not None and persona.engine is not None else None
    source = sens or DEFAULT_SENSITIVITY
    return {axis: float(getattr(source, axis)) for axis in AFFINITY_AXES}


def stage_pace_of(persona: Persona | None) -> float:
    if persona is None or persona.engine is None:
        return 1.0
    return float(persona.engine.affinity.stage_pace)


def expression_delay_of(persona: Persona | None) -> float:
    if persona is None or persona.engine is None:
        return 0.0
    return float(persona.engine.affinity.expression_delay)


TOP_STAGE: Final[str] = STAGES[-1]


def max_stage_of(persona: Persona | None) -> str:
    """ペルソナの段階の上限（engine.affinity.max_stage）。無ければ上限なし（lover）。"""
    if persona is None or persona.engine is None or persona.engine.affinity.max_stage is None:
        return TOP_STAGE
    return persona.engine.affinity.max_stage


def cap_stage(stage: str, max_stage: str = TOP_STAGE) -> str:
    """段階を上限までに収める（不明な段階は acquaintance）。"""
    stage = stage if stage in STAGES else STAGES[0]
    cap = max_stage if max_stage in STAGES else TOP_STAGE
    return STAGES[min(STAGES.index(stage), STAGES.index(cap))]


# ---------------------------------------------------------------------------
# 変化量（A4 / A5）
# ---------------------------------------------------------------------------


def turn_delta(scores: Mapping[str, int], sensitivity: Mapping[str, float], config: AffinityConfig) -> dict[str, float]:
    """1 ターンの評価（軸ごとに -2〜+2）→ 変化量（感度でスケールし、1 ターンの上限で切る）。"""
    delta: dict[str, float] = {}
    for axis in AFFINITY_AXES:
        raw = int(scores.get(axis, 0))
        score = max(MIN_SCORE, min(MAX_SCORE, raw))
        sens = float(sensitivity.get(axis, 0.0))
        if score == 0 or sens <= 0:
            continue
        value = _clamp(score * config.points_per_step * sens, -config.per_turn_cap, config.per_turn_cap)
        if value:
            delta[axis] = _round(value)
    return delta


def sum_deltas(deltas: list[dict[str, float]]) -> dict[str, float]:
    total: dict[str, float] = {}
    for delta in deltas:
        for axis, value in delta.items():
            total[axis] = _round(total.get(axis, 0.0) + value)
    return {axis: value for axis, value in total.items() if value}


def apply_daily_cap(
    delta: Mapping[str, float], record: AffinityRecord, today: date, config: AffinityConfig
) -> tuple[dict[str, float], dict[str, float]]:
    """1 日（JST）の合計の上限を適用する。戻り値 = (適用できる変化量, 更新後のその日の合計)。

    その日の合計を [-per_day_cap, +per_day_cap] に収める（|Σ| ≤ per_day_cap）。日付が変わっていれば合計は 0 から。
    """
    daily = dict(record.daily_delta) if record.daily_date == today else {}
    applied: dict[str, float] = {}
    for axis, value in delta.items():
        current = float(daily.get(axis, 0.0))
        new_total = _clamp(current + value, -config.per_day_cap, config.per_day_cap)
        change = _round(new_total - current)
        if change:
            applied[axis] = change
            daily[axis] = _round(new_total)
    return applied, {axis: value for axis, value in daily.items() if value}


def apply_values(values: Mapping[str, float], delta: Mapping[str, float]) -> tuple[dict[str, float], dict[str, float]]:
    """値に変化量を足して 0〜100 に収める。戻り値 = (新しい値, 実際の変化量)。"""
    new_values = {axis: float(values.get(axis, INITIAL_VALUES.get(axis, 0.0))) for axis in AFFINITY_AXES}
    actual: dict[str, float] = {}
    for axis, change in delta.items():
        before = new_values[axis]
        after = _round(_clamp(before + change, VALUE_MIN, VALUE_MAX))
        new_values[axis] = after
        if after != before:
            actual[axis] = _round(after - before)
    return new_values, actual


# ---------------------------------------------------------------------------
# 段階（A7）
# ---------------------------------------------------------------------------


def effective_threshold(stage: str, pace: float, config: AffinityConfig) -> StageThreshold:
    """stage_pace で初期値からの距離を割った閾値（pace が大きいほど早く上がる）。上限は max_threshold。"""
    base = config.thresholds[stage]
    pace = max(pace, 0.01)

    def scale(axis: str, threshold: float) -> float:
        if threshold <= 0:
            return 0.0
        start = INITIAL_VALUES.get(axis, 0.0)
        return _round(min(config.max_threshold, start + max(threshold - start, 0.0) / pace))

    return StageThreshold(
        closeness=scale("closeness", base.closeness),
        trust=scale("trust", base.trust),
        romance=scale("romance", base.romance),
    )


def qualifies(values: Mapping[str, float], stage: str, pace: float, config: AffinityConfig) -> bool:
    """その段階の条件（閾値）を満たしているか。acquaintance は常に満たす。"""
    if stage == "acquaintance":
        return True
    threshold = effective_threshold(stage, pace, config)
    return (
        float(values.get("closeness", 0.0)) >= threshold.closeness
        and float(values.get("trust", 0.0)) >= threshold.trust
        and float(values.get("romance", 0.0)) >= threshold.romance
    )


def advance_stage(
    record: AffinityRecord,
    *,
    now: datetime,
    added_user_turns: int,
    pace: float,
    config: AffinityConfig,
    max_stage: str = TOP_STAGE,
) -> AffinityRecord:
    """ヒステリシス付きの段階の遷移。record.values は更新後の値であること。

    max_stage（ペルソナの上限）より上には昇格させない。既に上にいる（ペルソナの上限を後から下げた）ペアは
    上限の段階に戻す（値は変えない）。
    """
    stage = record.stage if record.stage in STAGES else "acquaintance"
    index = STAGES.index(stage)
    cap_index = STAGES.index(cap_stage(TOP_STAGE, max_stage))
    if index > cap_index:
        return replace(
            record,
            stage=STAGES[cap_index],
            stage_changed_at=now,
            stage_candidate=None,
            stage_candidate_since=None,
            stage_candidate_turns=0,
        )
    tension = record.tension
    stage_changed_at = record.stage_changed_at
    candidate = record.stage_candidate
    candidate_since = record.stage_candidate_since
    candidate_turns = record.stage_candidate_turns

    # --- 降格の判定（緊張が高い状態が demotion_days 続いたら 1 段だけ）
    high = tension >= config.demotion_tension
    tension_high_since = (record.tension_high_since or now) if high else None
    if (
        tension_high_since is not None
        and index > 0
        and (now - tension_high_since).total_seconds() >= config.demotion_days * _SECONDS_PER_DAY
    ):
        return replace(
            record,
            stage=STAGES[index - 1],
            stage_changed_at=now,
            stage_candidate=None,
            stage_candidate_since=None,
            stage_candidate_turns=0,
            # さらに下がるには、ここからもう一度 demotion_days 続く必要がある
            tension_high_since=now,
        )

    # --- 昇格の判定（次の段階の条件を続けて満たし、日数と発言数が足りたら 1 段だけ。上限まで）
    if index + 1 <= cap_index:
        target = STAGES[index + 1]
        if qualifies(record.values, target, pace, config) and tension < config.promotion_max_tension:
            if candidate != target or candidate_since is None:
                candidate, candidate_since, candidate_turns = target, now, 0
            else:
                candidate_turns += max(added_user_turns, 0)
            days = (jst_date(now) - jst_date(candidate_since)).days
            min_days = config.promotion_min_days.get(target, 1)
            if days >= min_days and candidate_turns >= config.promotion_min_turns.get(target, 0):
                return replace(
                    record,
                    stage=target,
                    stage_changed_at=now,
                    stage_candidate=None,
                    stage_candidate_since=None,
                    stage_candidate_turns=0,
                    tension_high_since=tension_high_since,
                )
        else:
            candidate, candidate_since, candidate_turns = None, None, 0
    else:
        candidate, candidate_since, candidate_turns = None, None, 0

    return replace(
        record,
        stage=stage,
        stage_changed_at=stage_changed_at,
        stage_candidate=candidate,
        stage_candidate_since=candidate_since,
        stage_candidate_turns=candidate_turns,
        tension_high_since=tension_high_since,
    )


# ---------------------------------------------------------------------------
# 減衰（A6）
# ---------------------------------------------------------------------------


def decay(record: AffinityRecord, *, now: datetime, config: AffinityConfig) -> tuple[AffinityRecord, dict[str, float]]:
    """緊張の軸と独占欲を、前回の減衰からの経過時間に応じて 0 に近づける。戻り値 = (新しい状態, 変化量)。

    好意の軸は変えない。基準時刻（last_decayed_at）が無ければ記録するだけ。丸めで変化が 0 になる短い経過時間では
    基準時刻を進めない（何度呼んでも減衰が失われない）。減衰の基準時刻は、緊張が 0 から増えたときに評価側が設定する
    （start_decay_clock）。
    """
    if record.last_decayed_at is None:
        return replace(record, last_decayed_at=now), {}
    elapsed_days = (now - record.last_decayed_at).total_seconds() / _SECONDS_PER_DAY
    if elapsed_days <= 0:
        return record, {}
    half_lives = dict.fromkeys(TENSION_AXES, config.tension_half_life_days)
    half_lives["possessiveness"] = config.possessiveness_half_life_days
    values = dict(record.values)
    change: dict[str, float] = {}
    for axis, half_life in half_lives.items():
        before = float(values.get(axis, 0.0))
        if before <= 0:
            continue
        after = before * 0.5 ** (elapsed_days / half_life)
        if after < config.decay_min_change:
            after = 0.0
        after = _round(after)
        if after != before:
            values[axis] = after
            change[axis] = _round(after - before)
    if not change:
        return record, {}
    return replace(record, values=values, last_decayed_at=now), change


DECAY_AXES: Final[tuple[str, ...]] = (*TENSION_AXES, "possessiveness")


def start_decay_clock(before: AffinityRecord, after: AffinityRecord, now: datetime) -> AffinityRecord:
    """減衰の対象（緊張・独占欲）が 0 だった状態から増えたら、減衰の基準時刻を now にする。"""
    was_zero = all(before.value(axis) <= 0 for axis in DECAY_AXES)
    if before.last_decayed_at is None or was_zero:
        return replace(after, last_decayed_at=now)
    return after


def days_between(earlier: datetime, later: datetime) -> int:
    """JST の暦日の差（0 = 同じ日）。"""
    return (jst_date(later) - jst_date(earlier)).days


def apply_evaluation(
    record: AffinityRecord,
    requested: Mapping[str, float],
    *,
    now: datetime,
    evaluated_turns: int,
    newest: datetime | None,
    pace: float,
    config: AffinityConfig,
    max_stage: str = TOP_STAGE,
) -> tuple[AffinityRecord, dict[str, float]]:
    """評価 1 回分の変化量（1 ターンの上限を適用済みの合計）を状態に反映する。戻り値 = (新しい状態, 実際の変化量)。

    1 日（JST）の上限 → 0〜100 → 減衰の基準時刻 → 段階のヒステリシス（ペルソナの上限 max_stage まで）。
    サービスと評価ハーネスが同じ関数を使う。
    """
    today = jst_date(now)
    capped, _ = apply_daily_cap(requested, record, today, config)
    values, applied = apply_values(record.values, capped)
    # その日の合計は実際に動いた量で数える（0〜100 の端で止まった分は数えない）
    _, daily = apply_daily_cap(applied, record, today, config)
    evaluated_until = record.evaluated_until
    if newest is not None:
        evaluated_until = max(newest, evaluated_until) if evaluated_until is not None else newest
    updated = replace(
        record,
        values=values,
        daily_date=today,
        daily_delta=daily,
        evaluated_until=evaluated_until,
        user_turns=record.user_turns + evaluated_turns,
    )
    updated = start_decay_clock(record, updated, now)
    updated = advance_stage(
        updated, now=now, added_user_turns=evaluated_turns, pace=pace, config=config, max_stage=max_stage
    )
    return updated, applied


def apply_daily(
    record: AffinityRecord, *, now: datetime, pace: float, config: AffinityConfig, max_stage: str = TOP_STAGE
) -> tuple[AffinityRecord, dict[str, float]]:
    """日次処理 1 回分（減衰 → 段階の見直し）。戻り値 = (新しい状態, 減衰による変化量)。"""
    decayed, change = decay(record, now=now, config=config)
    return (
        advance_stage(decayed, now=now, added_user_turns=0, pace=pace, config=config, max_stage=max_stage),
        change,
    )
