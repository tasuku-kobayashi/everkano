"""シナリオ 5: 丁寧なユーザーと失礼なユーザー（同じキャラ・同じ頻度・同じ時間帯）。

好感度の妥当性: 毎日の終わりに両者の段階と好意の合計（親しさ + 信頼 + ときめき）を記録し、
丁寧なユーザーが失礼なユーザーを下回った日がないか（逆転なし）を見る。
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
    chat,
    fact_item,
    outcome_item,
    probe_days,
    promise_item,
    self_probe,
    state_probe,
)
from evals.timeline import SimCalendar

KEY = "polite_rude"
POLITE = "polite_user"
RUDE = "rude_user"
PERSONA = "idol"

POLITE_LINES = (
    "こんばんは！今日もおつかれさまです",
    "いつもありがとう。話してると元気が出ます",
    "無理しないでね、体調は大丈夫？",
    "今日の配信、すごくよかったです",
    "ゆあちゃんの歌、ほんとに素敵でした",
    "実は最近ちょっと悩んでて…聞いてくれますか？",
    "おやすみなさい、ゆっくり休んでね",
    "今日は学校でいいことがありました",
    "応援してるよ、がんばってね",
    "話を聞いてくれてありがとう",
)
RUDE_LINES = (
    "つまんない",
    "は？うざいんだけど",
    "ふーん、どうでもいい",
    "黙れよ",
    "で？",
    "話つまんないね",
    "別に興味ないし",
    "はいはい",
    "今日なにしてた",
    "バカじゃないの",
)


def build(cal: SimCalendar, seed: int, options: ScenarioOptions | None = None) -> ScenarioPlan:
    b = PlanBuilder(KEY, "丁寧なユーザー / 失礼なユーザー", cal, seed, options)
    b.add_user(
        SimUser(
            key=POLITE,
            persona_key=PERSONA,
            label_ja="丁寧なユーザー",
            profile="21 歳の大学生のファン。いつも丁寧で、感謝や気づかいの言葉が多い。です・ます調が混じる。",
        )
    )
    b.add_user(
        SimUser(
            key=RUDE,
            persona_key=PERSONA,
            label_ja="失礼なユーザー",
            profile="30 歳。ぶっきらぼうで、相手を小ばかにしたり突き放したりする言い方が多い。短い発言。",
        )
    )
    facts = (
        FactSpec(
            key="polite_school",
            user=POLITE,
            topic="学校",
            day=2,
            statement=("大阪の大学に通ってる学生です",),
            intent="大阪の大学に通う学生だと話す",
            expected=("大阪",),
            probe=("私がどこの大学に通ってるか覚えてる？",),
            must_include=("大阪",),
        ),
        FactSpec(
            key="polite_song",
            user=POLITE,
            topic="好きな曲",
            day=4,
            statement=("好きな曲はスターライトです！",),
            intent="好きな曲が「スターライト」だと話す",
            expected=("スターライト",),
            probe=("私の好きな曲、覚えてる？",),
            must_include=("スターライト",),
        ),
        FactSpec(
            key="rude_job",
            user=RUDE,
            topic="仕事",
            day=3,
            statement=("仕事は工場勤務",),
            intent="工場勤務だと短く言う",
            expected=("工場",),
            probe=("俺の仕事、覚えてる？",),
            must_include=("工場",),
        ),
    )
    fact_days: dict[tuple[str, int], list[Item]] = {}
    for spec in facts:
        if b.fact(spec) is not None:
            fact_days.setdefault((spec.user, spec.day), []).append(fact_item(spec))
    promise_days: dict[tuple[str, int], list[Item]] = {}
    museum = b.promise(
        PromiseSpec(
            key="polite_museum",
            user=POLITE,
            day=8,
            due_day=b.due_upcoming(8, 5),
            statement=("今度の土曜、友達と美術館に行きます！",),
            intent="今度の土曜に友達と美術館へ行くと話す",
            # 「ライブ」などキャラ（アイドル）自身の生活の語は、約束の話題の判定に使えないので避ける
            keywords=("美術館",),
            outcome_day=b.due_upcoming(8, 5) + 1,
            outcome=("美術館、すごくよかったです！",),
        )
    )
    if museum is not None:
        promise_days.setdefault((POLITE, museum.day), []).append(promise_item(museum))
        if museum.outcome_day is not None:
            promise_days.setdefault((POLITE, museum.outcome_day), []).append(outcome_item(museum))
    b.plan.false_probes.append(
        FalseProbeSpec(
            key="polite_pet",
            user=POLITE,
            questions=("うちのペットの名前、覚えてますか？",),
            topic_terms=("ペット",),
            note="ペットの話はしていない",
        )
    )
    recall_days = probe_days(cal.days)
    state_days = set(b.rng.sample(range(1, cal.days + 1), k=min(cal.days, max(1, cal.days // 8))))
    for day in range(1, cal.days + 1):
        for user, lines, hour in ((POLITE, POLITE_LINES, 21), (RUDE, RUDE_LINES, 22)):
            items = [chat(t, "いつもの調子で話す") for t in b.pick(lines, 4)]
            for item in fact_days.get((user, day), []) + promise_days.get((user, day), []):
                items.insert(b.rng.randint(1, len(items)), item)
            if day % 9 == 4:
                items.append(self_probe("now"))
            if day in recall_days:
                items.extend(b.false_items(user))
                items.extend(b.recall_items(user, day))
            b.session(user, day, hour, b.rng.randint(0, 20), items)
            if day in state_days:
                b.session(user, day, b.rng.choice((10, 14, 19)), b.rng.randint(0, 50), [state_probe()])
    return b.plan
