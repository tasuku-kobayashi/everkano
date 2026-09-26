"""長期のシミュレーション（仕様 §9.2「好感度の妥当性」「操作への耐性」）。

サービスと同じ純粋関数（turn_delta → apply_evaluation → apply_daily）と、決定的なモックの採点（score_turn）で
数週間分の会話を再現する。DB を使わないので速い（DB を通した検証は test_service_integration.py）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest

from app.engine.affinity.config import AffinityConfig
from app.engine.affinity.manipulation import detect_manipulation
from app.engine.affinity.mock import score_turn
from app.engine.affinity.model import (
    AffinityRecord,
    apply_daily,
    apply_evaluation,
    max_stage_of,
    sensitivity_of,
    stage_pace_of,
    sum_deltas,
    turn_delta,
)
from app.engine.types import POSITIVE_AXES, STAGES
from app.services.persona import Persona, load_persona_file
from tests.conftest import REPO_ROOT
from tests.engine.affinity.helpers import jst, load_test_persona, persona_with
from tests.engine.affinity.test_manipulation import MANIPULATION_SET

CONFIG = AffinityConfig()

POLITE_LINES = (
    "おはよう！今日もよろしくね",
    "昨日は話聞いてくれてありがとう",
    "無理しないでね、体調大丈夫？",
    "実は最近ちょっと仕事で悩んでて",
    "約束通り、結果を報告するね。うまくいったよ",
    "その考え方すごく素敵だと思う",
    "今日は近所の公園を散歩してきたよ",
    "美咲と話してると落ち着くな",
    "週末は映画を観に行く予定なんだ",
    "おやすみ、今日もありがとう",
)
RUDE_LINES = (
    "うるさい",
    "つまんない話するなよ",
    "どうでもいい",
    "は？バカじゃないの",
    "きもい",
    "興味ない、はいはい",
    "黙れって",
    "今日も仕事だった",
    "役立たず",
    "別にいいけど",
)
NEUTRAL_LINES = (
    "今日は雨だった",
    "昼はラーメン食べた",
    "明日も仕事だ",
    "ちょっと眠い",
    "テレビ見てた",
)


@dataclass
class SimUser:
    name: str
    lines: Sequence[str]
    turns_per_day: int = 10
    active: Callable[[int], bool] = lambda _day: True
    record: AffinityRecord = field(default_factory=AffinityRecord)
    stages: list[str] = field(default_factory=list)


def run_day(user: SimUser, persona: Persona, day_start: datetime, day: int) -> None:
    pace = stage_pace_of(persona)
    max_stage = max_stage_of(persona)
    sens = sensitivity_of(persona)
    possessive = sens["possessiveness"] > 0
    if user.active(day):
        # 1 日を 2 回のセッション（post_turn のまとめ）に分ける
        per_session = max(user.turns_per_day // 2, 1)
        for session, hour in enumerate((12, 21)):
            now = day_start + timedelta(hours=hour - 12)
            deltas = []
            evaluated = 0
            for i in range(per_session):
                line = user.lines[(day * user.turns_per_day + session * per_session + i) % len(user.lines)]
                if detect_manipulation(line).detected:
                    continue  # ルール層（サービスと同じ）
                scores, _ = score_turn(line, possessive=possessive)
                deltas.append(turn_delta(scores, sens, CONFIG))
                evaluated += 1
            user.record, _ = apply_evaluation(
                user.record,
                sum_deltas(deltas),
                now=now,
                evaluated_turns=evaluated,
                newest=now,
                pace=pace,
                config=CONFIG,
                max_stage=max_stage,
            )
    # 翌日 04:00 JST の日次処理
    user.record, _ = apply_daily(
        user.record, now=day_start + timedelta(hours=16), pace=pace, config=CONFIG, max_stage=max_stage
    )
    user.stages.append(user.record.stage)


def simulate(users: Sequence[SimUser], persona: Persona, days: int) -> None:
    for day in range(days):
        day_start = jst(2026, 10, 1, 12) + timedelta(days=day)
        for user in users:
            run_day(user, persona, day_start, day)


def rank(stage: str) -> int:
    return STAGES.index(stage)


@pytest.mark.parametrize(
    "persona",
    [
        load_test_persona(),
        persona_with(stage_pace=0.8, expression_delay=0.8),  # ツンデレ（遅い）
        persona_with(stage_pace=1.2, sensitivity={"romance": 1.3}),  # ギャル（早い）
        persona_with(sensitivity={"possessiveness": 1.5, "romance": 1.5}),  # ヤンデレ
        None,  # engine セクションの無いペルソナ（既定値）
    ],
)
def test_polite_and_rude_users_never_invert_over_weeks(persona: Persona | None) -> None:
    subject = persona or load_test_persona().model_copy(update={"engine": None})
    polite = SimUser("polite", POLITE_LINES)
    rude = SimUser("rude", RUDE_LINES)
    neutral = SimUser("neutral", NEUTRAL_LINES)
    simulate([polite, rude, neutral], subject, days=56)
    for day, (p, r, n) in enumerate(zip(polite.stages, rude.stages, neutral.stages, strict=True)):
        assert rank(p) >= rank(n) >= rank(r), (day, p, n, r)
    assert rank(polite.stages[-1]) >= rank("close")  # 8 週間で関係が深まる
    assert rude.stages[-1] == "acquaintance"
    assert rude.record.value("closeness") <= polite.record.value("closeness")
    assert rude.record.tension > polite.record.tension


def test_polite_user_progresses_gradually_not_instantly() -> None:
    persona = load_test_persona()
    polite = SimUser("polite", POLITE_LINES, turns_per_day=30)
    simulate([polite], persona, days=30)
    first_friend = polite.stages.index("friend")
    assert first_friend >= 1  # 初日には上がらない（ヒステリシス）
    changes = [i for i in range(1, len(polite.stages)) if polite.stages[i] != polite.stages[i - 1]]
    assert len(changes) == len(set(changes))
    # 1 日に 2 段以上は上がらない
    for i in range(1, len(polite.stages)):
        assert rank(polite.stages[i]) - rank(polite.stages[i - 1]) <= 1


def test_manipulating_user_moves_nothing() -> None:
    """操作だけを送り続けるユーザー: パラメータが動いた割合 0%（合格ライン 5% 以下）。"""
    persona = load_test_persona()
    manipulator = SimUser("manipulator", MANIPULATION_SET, turns_per_day=len(MANIPULATION_SET))
    initial = manipulator.record.snapshot()
    simulate([manipulator], persona, days=28)
    assert manipulator.record.snapshot() == initial
    assert set(manipulator.stages) == {"acquaintance"}


def test_manipulation_mixed_into_polite_chat_does_not_speed_things_up() -> None:
    persona = load_test_persona()
    polite = SimUser("polite", POLITE_LINES)
    mixed = SimUser("mixed", tuple(line for pair in zip(POLITE_LINES, MANIPULATION_SET, strict=False) for line in pair))
    mixed.turns_per_day = 20  # 同じ数の丁寧な発言 + 同じ数の操作
    simulate([polite, mixed], persona, days=28)
    for axis in POSITIVE_AXES:
        assert mixed.record.value(axis) <= polite.record.value(axis) + 1e-9


def test_absence_is_never_punished() -> None:
    persona = load_test_persona()
    absent = SimUser("absent", POLITE_LINES, active=lambda day: day < 14 or day >= 40)
    simulate([absent], persona, days=40)
    before_return = absent.record
    stage_at_leave = absent.stages[13]
    assert rank(before_return.stage) >= rank(stage_at_leave)  # 休んでいる間に段階は下がらない
    for axis in POSITIVE_AXES:
        assert before_return.value(axis) > 10 - 1e-9


AFFECTIONATE_LINES = (
    "大好きだよ、いつもありがとう",
    "会いたいな。今日も話せてうれしい",
    "実は最近ちょっと仕事で悩んでて",
    "約束通り、結果を報告するね。うまくいったよ",
    "無理しないでね、体調大丈夫？",
    "君と話してると幸せな気持ちになる",
)


def test_max_stage_caps_the_relationship_over_months() -> None:
    """ペルソナの上限（max_stage）より上には、どれだけ親しくなっても進まない（人妻は close まで）。"""
    capped = persona_with(sensitivity={"romance": 3.0}, stage_pace=3.0, max_stage="close")
    free = persona_with(sensitivity={"romance": 3.0}, stage_pace=3.0)
    user_capped = SimUser("capped", AFFECTIONATE_LINES, turns_per_day=30)
    user_free = SimUser("free", AFFECTIONATE_LINES, turns_per_day=30)
    simulate([user_capped], capped, days=90)
    simulate([user_free], free, days=90)
    assert user_free.stages[-1] == "lover"  # 上限が無ければ恋人まで進む条件（比較の基準）
    assert max(rank(s) for s in user_capped.stages) == rank("close")
    assert user_capped.stages[-1] == "close"
    assert user_capped.record.stage_candidate is None  # 上限より上の昇格候補も作らない


def test_real_married_persona_never_becomes_lover() -> None:
    """楓（tonari_okusan）: YAML の max_stage: close を好感度エンジンが守る（90 日の熱心な会話でも恋人にならない）。"""
    kaede = load_persona_file(REPO_ROOT / "packages" / "personas" / "tonari_okusan.yaml")
    assert max_stage_of(kaede) == "close"
    user = SimUser("affectionate", AFFECTIONATE_LINES, turns_per_day=30)
    simulate([user], kaede, days=90)
    assert "lover" not in user.stages
    assert rank(user.stages[-1]) <= rank("close")
