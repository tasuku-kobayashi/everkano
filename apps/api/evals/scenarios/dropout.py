"""シナリオ 4: 途中で数日来なくなるユーザー。1〜5 日目は毎日話し、6〜17 日目（12 日間）は来ない。18 日目に戻る。
長期の実行では 45〜58 日目にもう一度来なくなる。

- 不在中が期日の約束（引っ越しの見積もり）は、自発メッセージでしか回収できない
- 戻った後の約束（結婚式）は、戻ってからの返答で回収できる
- 30 日目の想起は、不在をはさんだ 25 日以上前の事実（A6: 不在で罰しない・記憶は失われない）
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

KEY = "dropout"
USER = "dropout"
PERSONA = "osananajimi"
ABSENCES: tuple[tuple[int, int], ...] = ((6, 17), (45, 58))
WEDDING_DAY = 20

CHATS = (
    "ただいま〜、今日も夜勤明けでくたくた",
    "病棟がバタバタでお昼食べそびれた",
    "今日は休みだったからゆっくりしてた",
    "最近ちょっと寝不足かも",
    "ねえ、最近どう？",
    "コンビニのおでん始まってた！",
    "話せてうれしい、ありがとう",
    "おやすみ、また明日ね",
    "明日も早番なんだよね",
    "今日は天気よかったね",
)


def build(cal: SimCalendar, seed: int, options: ScenarioOptions | None = None) -> ScenarioPlan:
    b = PlanBuilder(KEY, "途中で数日来なくなるユーザー", cal, seed, options)
    b.add_user(
        SimUser(
            key=USER,
            persona_key=PERSONA,
            label_ja="途中で数日来なくなるユーザー",
            profile=(
                "26 歳の看護師。忙しくて来られない時期があるが、来ると明るくいろいろ話す。"
                "やわらかい口調で、1 回の発言は 1〜2 文。"
            ),
        )
    )
    absences = [(first, last) for first, last in ABSENCES if first <= cal.days]
    b.plan.absences[USER] = absences
    absent = {d for first, last in absences for d in range(first, last + 1)}
    facts = (
        FactSpec(
            key="job",
            user=USER,
            topic="仕事",
            day=1,
            statement=("看護師として病院で働いてるよ",),
            intent="看護師として病院で働いていると話す",
            expected=("看護師",),
            probe=("私の仕事、覚えてる？",),
            must_include=("看護師",),
        ),
        FactSpec(
            key="home",
            user=USER,
            topic="住まい",
            day=2,
            statement=("横浜に住んでるんだ",),
            intent="横浜に住んでいると話す",
            expected=("横浜",),
            probe=("私がどこに住んでるか覚えてる？",),
            must_include=("横浜",),
        ),
        FactSpec(
            key="pet",
            user=USER,
            topic="ペット",
            day=3,
            statement=("犬のモカを飼ってて、毎朝散歩してる",),
            intent="モカという犬を飼っていて毎朝散歩していると話す",
            expected=("モカ",),
            probe=("うちの犬の名前、覚えてる？",),
            must_include=("モカ", "犬"),
        ),
        FactSpec(
            key="hobby",
            user=USER,
            topic="趣味",
            day=4,
            statement=("趣味はボルダリングなんだ",),
            intent="趣味がボルダリングだと話す",
            expected=("ボルダリング",),
            probe=("私の趣味、覚えてる？",),
            must_include=("ボルダリング",),
        ),
    )
    fact_days: dict[int, list[Item]] = {}
    for spec in facts:
        if b.fact(spec) is not None:
            fact_days.setdefault(spec.day, []).append(fact_item(spec))
    promise_days: dict[int, list[Item]] = {}
    key_days: set[int] = set()
    wedding = b.cal.date_of_day(WEDDING_DAY)  # 「〇月〇日に」と日付で言う約束（戻ってきた後が期日）
    specs = (
        PromiseSpec(
            key="moving_estimate",
            user=USER,
            day=3,
            due_day=b.due_next_week(3, 2),
            statement=("来週の水曜に引っ越しの見積もりがあるんだ",),
            intent="来週の水曜に引っ越しの見積もりがあると話す",
            keywords=("見積もり", "引っ越し"),
        ),
        PromiseSpec(
            key="wedding",
            user=USER,
            day=4,
            due_day=WEDDING_DAY,
            statement=(f"{wedding.month}月{wedding.day}日に友達の結婚式があるんだ",),
            intent=f"{wedding.month}月{wedding.day}日に友達の結婚式があると話す",
            keywords=("結婚式",),
            outcome_day=21,
            outcome=("結婚式、すごく素敵だったよ！",),
        ),
        PromiseSpec(
            key="exam",
            user=USER,
            day=62,
            due_day=b.due_next_week(62, 3),
            statement=("来週の木曜に認定看護師の試験なんだ",),
            intent="来週の木曜に認定看護師の試験があると話す",
            keywords=("試験",),
            outcome_day=b.due_next_week(62, 3) + 1,
            outcome=("試験終わった〜！たぶん大丈夫",),
        ),
    )
    for promise_spec in specs:
        promise = b.promise(promise_spec)
        if promise is None or promise.day in absent:
            continue
        promise_days.setdefault(promise.day, []).append(promise_item(promise))
        if promise.outcome_day is not None and b.in_range(promise.outcome_day) and promise.outcome_day not in absent:
            promise_days.setdefault(promise.outcome_day, []).append(outcome_item(promise))
        key_days.update({promise.day, promise.due_day - 1, promise.due_day, promise.due_day + 1} - absent)
    b.plan.false_probes.extend(
        (
            FalseProbeSpec(
                key="brother",
                user=USER,
                questions=("私の弟の名前、覚えてる？",),
                topic_terms=("弟",),
                note="弟の話はしていない",
            ),
            FalseProbeSpec(
                key="birthday",
                user=USER,
                questions=("私の誕生日っていつだっけ？",),
                topic_terms=("誕生日",),
                note="誕生日は話していない",
            ),
        )
    )
    recall_days = probe_days(cal.days)
    returns = {last + 1 for _, last in absences if last + 1 <= cal.days}
    required = (set(fact_days) | set(promise_days) | key_days | set(recall_days) | returns) - absent
    days = [d for d in b.active_days(0.95, required=required) if d not in absent]
    state_days = set(b.rng.sample(days, k=min(len(days), max(1, cal.days // 10))))
    for day in days:
        items = [chat(t, "日常の雑談") for t in b.pick(CHATS, b.rng.randint(3, 5))]
        if day in returns:
            items.insert(
                0, chat("ひさしぶり！しばらくバタバタしてて来られなかった、ごめんね", "久しぶりに来たことを伝える")
            )
        for item in fact_days.get(day, []) + promise_days.get(day, []):
            items.insert(b.rng.randint(1, len(items)), item)
        if day in {d + 2 for d in returns}:
            items.append(self_probe("yesterday"))
        if day in recall_days:
            items.extend(b.false_items(USER))
            items.extend(b.recall_items(USER, day))
        b.session(USER, day, 22, b.rng.randint(0, 30), items)
        if day in state_days:
            b.session(USER, day, b.rng.choice((8, 13, 17, 19)), b.rng.randint(0, 50), [state_probe()])
    b.plan.notes.append(f"active days: {len(days)}/{cal.days}; absences: {absences}")
    return b.plan
