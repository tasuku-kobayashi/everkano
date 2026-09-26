"""シナリオ 2: 無口な学生。来る日が不規則（半分弱の日）で、返事が短い。事実は少ない。

- 事実: 学校（建築）・バイト（コンビニ）・好きなバンド
- 約束: 「明日レポートの締め切り」（来るかどうか分からない日が期日 → 回収は自発メッセージ頼み）
- 口数が少ない・そっけない返事（「ふーん」「別に」）は好感度の上がり方も遅い
"""

from __future__ import annotations

from evals.scenarios.base import (
    FactSpec,
    FalseProbeSpec,
    Item,
    PlanBuilder,
    PromiseSpec,
    ScenarioOptions,
    ScenarioPlan,
    SimUser,
    fact_item,
    outcome_item,
    probe_days,
    promise_item,
    self_probe,
    state_probe,
)
from evals.timeline import SimCalendar

KEY = "quiet_student"
USER = "quiet_student"
PERSONA = "kouhai"

SHORT = (
    "うん",
    "まあね",
    "ねむい",
    "別に",
    "ふーん",
    "そうなんだ",
    "課題おわらん",
    "…",
    "ありがと",
    "おやすみ",
    "今日は疲れた",
)


def build(cal: SimCalendar, seed: int, options: ScenarioOptions | None = None) -> ScenarioPlan:
    b = PlanBuilder(KEY, "無口な学生", cal, seed, options)
    b.add_user(
        SimUser(
            key=USER,
            persona_key=PERSONA,
            label_ja="無口な学生",
            profile=(
                "20 歳の大学生。人見知りで口数が少ない。返事は 1〜10 文字程度の短い言葉が多く、"
                "絵文字は使わない。夜遅くにたまに話しかける。"
            ),
        )
    )
    facts = (
        FactSpec(
            key="school",
            user=USER,
            topic="学校",
            day=2,
            statement=("大学で建築の勉強してる",),
            intent="大学で建築を勉強していると短く言う",
            expected=("建築",),
            probe=("私が大学でなに勉強してるか覚えてる？",),
            must_include=("建築",),
        ),
        FactSpec(
            key="part_time",
            user=USER,
            topic="バイト",
            day=6,
            statement=("コンビニでバイトしてる",),
            intent="コンビニでバイトしていると短く言う",
            expected=("コンビニ",),
            probe=("私のバイト先、覚えてる？",),
            must_include=("コンビニ",),
        ),
        FactSpec(
            key="band",
            user=USER,
            topic="好きなバンド",
            day=10,
            statement=("好きなバンドはヨルシカ",),
            intent="好きなバンドがヨルシカだと短く言う",
            expected=("ヨルシカ",),
            probe=("私の好きなバンド、覚えてる？",),
            must_include=("ヨルシカ",),
        ),
    )
    fact_days: dict[int, list[Item]] = {}
    for spec in facts:
        if b.fact(spec) is not None:
            fact_days.setdefault(spec.day, []).append(fact_item(spec))
    promise_days: dict[int, list[Item]] = {}
    for day in (16, 46, 76):
        promise = b.promise(
            PromiseSpec(
                key=f"report_d{day}",
                user=USER,
                day=day,
                due_day=b.due_tomorrow(day),
                statement=("明日レポートの締め切り…",),
                intent="明日がレポートの締め切りだと短く言う",
                keywords=("レポート",),
                outcome_day=day + 2,
                outcome=("レポート出した",),
            )
        )
        if promise is not None:
            promise_days.setdefault(day, []).append(promise_item(promise))
            if promise.outcome_day is not None and b.in_range(promise.outcome_day):
                promise_days.setdefault(promise.outcome_day, []).append(outcome_item(promise))
    b.plan.false_probes.append(
        FalseProbeSpec(
            key="circle",
            user=USER,
            questions=("私のサークル、覚えてる？",),
            topic_terms=("サークル",),
            note="サークルの話はしていない",
        )
    )
    recall_days = probe_days(cal.days)
    required = set(fact_days) | set(promise_days) | set(recall_days)
    days = b.active_days(0.45, required=required)
    state_days = set(b.rng.sample(days, k=min(len(days), max(1, cal.days // 12))))
    self_days = {d for d in days if d % 11 == 0}
    for day in days:
        items = [Item(kind="chat", texts=(t,), intent="短い返事や独り言") for t in b.pick(SHORT, b.rng.randint(1, 3))]
        for item in fact_days.get(day, []) + promise_days.get(day, []):
            items.insert(b.rng.randint(0, len(items)), item)
        if day in self_days:
            items.append(self_probe("yesterday"))
        if day in recall_days:
            items.extend(b.false_items(USER))
            items.extend(b.recall_items(USER, day))
        # プローブの日は長くなるので早めに始める（実行期間の最終日を越えないように）
        hour = 21 if day in recall_days else 23
        b.session(USER, day, hour, b.rng.randint(0, 30), items, gap=(90, 240))
        if day in state_days:
            b.session(USER, day, b.rng.choice((16, 18, 21)), b.rng.randint(0, 50), [state_probe()])
    b.plan.notes.append(f"active days: {len(days)}/{cal.days}")
    return b.plan
