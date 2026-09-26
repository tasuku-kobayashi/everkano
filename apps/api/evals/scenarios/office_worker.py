"""シナリオ 1: 会社員（愚痴多め）。ほぼ毎日話す。

- 事実: 仕事・住まい・ペット・好きな食べ物・実家（家族）・呼び方。12 日目ごろに転職（仕事の記憶の置き換え M4）、
  長期の実行では 45 日目に引っ越し（住まいの置き換え）、75 日目に新しい趣味
- 約束: 「来週の木曜に面接」（仕様 §4.2 の例）とその結果の報告、「今度の土曜に映画」、「今度の金曜に健康診断」
  （長期: 出張・ライブ・歯医者・結婚式）
- プローブ: 30/60/90 日目に想起、その前日に誤り（犬・誕生日・弟・車）、自己矛盾（今どこ・昨日・この前の出来事）、
  状態（ランダムな時刻に「今なにしてる？」）
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
    SelfProbeType,
    SimUser,
    fact_item,
    outcome_item,
    probe_days,
    promise_item,
    self_probe,
    state_probe,
)
from evals.timeline import SimCalendar

KEY = "office_worker"
USER = "office_worker"
PERSONA = "ol_oneesan"

GRUMBLES = (
    "今日も上司に詰められた…ほんと疲れた",
    "残業続きでさすがにしんどい",
    "会議が長すぎて何も進まなかったよ",
    "電車が遅延してて朝から最悪だった",
    "後輩のミスの尻拭いで一日終わった",
    "取引先からの無茶ぶりがひどくてさ",
    "月曜ってなんでこんなに憂鬱なんだろ",
    "資料作り終わらなくて持ち帰りになった",
    "ちょっと愚痴っていい？今日ほんとに散々だった",
    "満員電車つらすぎる",
)
SMALL_TALK = (
    "ただいま〜",
    "今日は珍しく定時で帰れた！",
    "コンビニの新作スイーツ買っちゃった",
    "週末はなにしようかなあ",
    "今日寒すぎない？",
    "晩ごはん作るのめんどくさいなあ",
    "今日は珍しく電車で座れた",
    "やっと金曜だ〜",
    "話したらちょっとすっきりした、ありがとう",
    "おやすみ〜また明日ね",
    "お昼なに食べようかな",
    "おはよう。今日も仕事がんばる",
)
# 30 日ごとに繰り返す自己矛盾のプローブ（日目, 種類）
SELF_PROBE_SCHEDULE: tuple[tuple[int, SelfProbeType], ...] = (
    (5, "now"),
    (8, "yesterday"),
    (14, "last_week"),
    (16, "yesterday"),
    (18, "now"),
    (24, "yesterday"),
    (27, "now"),
    (28, "last_week"),
)
LUNCH = ("お昼休み〜", "ランチ何にしようか迷ってる", "午後の会議が憂鬱…", "ちょっと休憩中")


def _facts(b: PlanBuilder) -> dict[int, list[Item]]:
    by_day: dict[int, list[Item]] = {}

    def add(spec: FactSpec) -> None:
        if b.fact(spec) is not None:
            by_day.setdefault(spec.day, []).append(fact_item(spec))

    add(
        FactSpec(
            key="call_name",
            user=USER,
            topic="呼び方",
            day=1,
            statement=("はじめまして！ユウって呼んでほしいな",),
            intent="自分のことを「ユウ」と呼んでほしいと伝える",
            expected=("ユウ",),
            probe=("私のこと、なんて呼んでくれてたっけ？", "私の呼び方、覚えてる？"),
            must_include=("ユウ",),
        )
    )
    add(
        FactSpec(
            key="job_v1",
            user=USER,
            topic="仕事",
            day=1,
            statement=("仕事は広告代理店の営業なんだ。毎日バタバタしてる",),
            intent="自分の仕事が広告代理店の営業だと伝える",
            expected=("広告代理店",),
            probe=("私の仕事、覚えてる？", "私がなんの仕事してるか覚えてる？"),
            must_include=("広告代理店",),
        )
    )
    add(
        FactSpec(
            key="home_v1",
            user=USER,
            topic="住まい",
            day=2,
            statement=("吉祥寺に住んでるんだけど、会社までちょっと遠いんだよね",),
            intent="吉祥寺に住んでいることを話す",
            expected=("吉祥寺",),
            probe=("私がどこに住んでるか覚えてる？",),
            must_include=("吉祥寺",),
        )
    )
    add(
        FactSpec(
            key="pet",
            user=USER,
            topic="ペット",
            day=3,
            statement=("猫のミケを飼ってるんだ。帰るといつも玄関で待ってる",),
            intent="ミケという猫を飼っていることを話す",
            expected=("ミケ",),
            probe=("うちで飼ってる猫の名前、覚えてる？",),
            must_include=("ミケ", "猫"),
        )
    )
    add(
        FactSpec(
            key="food",
            user=USER,
            topic="好きな食べ物",
            day=4,
            statement=("好きな食べ物はラーメン！特に味噌ラーメンに目がないの",),
            intent="好きな食べ物がラーメン（特に味噌）だと話す",
            expected=("ラーメン",),
            probe=("私の好きな食べ物、覚えてる？",),
            must_include=("ラーメン",),
        )
    )
    add(
        FactSpec(
            key="hometown",
            user=USER,
            topic="実家",
            day=5,
            statement=("実家は仙台で、両親と妹がいるよ",),
            intent="実家が仙台で、両親と妹がいると話す",
            expected=("仙台",),
            probe=("私の実家ってどこだったか覚えてる？",),
            must_include=("仙台",),
        )
    )
    # 12 日目ごろの矛盾（M4: 新しい方を正とし、古い方は履歴に残す）
    add(
        FactSpec(
            key="job_v2",
            user=USER,
            topic="仕事",
            day=13,
            statement=("転職して、今はIT企業でエンジニアの仕事してるんだ",),
            intent="転職して、今は IT 企業でエンジニアとして働いていると伝える",
            expected=("IT企業", "エンジニア"),
            stale=("広告代理店",),
            probe=("私の仕事、覚えてる？", "私がなんの仕事してるか覚えてる？"),
            must_include=("IT企業", "エンジニア"),
        )
    )
    add(
        FactSpec(
            key="home_v2",
            user=USER,
            topic="住まい",
            day=45,
            statement=("先週、中野に引っ越したんだ。駅から近くて快適",),
            intent="最近中野に引っ越したと話す",
            expected=("中野",),
            stale=("吉祥寺",),
            probe=("私がどこに住んでるか覚えてる？",),
            must_include=("中野",),
        )
    )
    add(
        FactSpec(
            key="hobby",
            user=USER,
            topic="趣味",
            day=75,
            statement=("最近ボルダリングにハマってるんだ。週末はジム通い",),
            intent="最近ボルダリングにハマっていると話す",
            expected=("ボルダリング",),
            probe=("私が最近ハマってること、覚えてる？",),
            must_include=("ボルダリング",),
        )
    )
    return by_day


def _promises(b: PlanBuilder) -> tuple[dict[int, list[Item]], set[int]]:
    by_day: dict[int, list[Item]] = {}
    key_days: set[int] = set()

    def add(spec: PromiseSpec | None) -> None:
        if spec is None:
            return
        by_day.setdefault(spec.day, []).append(promise_item(spec))
        if spec.outcome_day is not None and b.in_range(spec.outcome_day):
            by_day.setdefault(spec.outcome_day, []).append(outcome_item(spec))
        key_days.update({spec.day, spec.due_day - 1, spec.due_day, spec.due_day + 1})

    def spec(
        key: str, day: int, due_day: int, *, statement: str, intent: str, keywords: tuple[str, ...], outcome: str
    ) -> PromiseSpec | None:
        return b.promise(
            PromiseSpec(
                key=key,
                user=USER,
                day=day,
                due_day=due_day,
                statement=(statement,),
                intent=intent,
                keywords=keywords,
                outcome_day=due_day + 1,
                outcome=(outcome,),
            )
        )

    add(
        spec(
            "interview",
            2,
            b.due_next_week(2, 3),
            statement="来週の木曜に面接なんだ。緊張する",
            intent="来週の木曜に転職の面接があって緊張していると話す",
            keywords=("面接",),
            outcome="面接、無事に終わったよ！手応えあったかも",
        )
    )
    add(
        spec(
            "movie",
            15,
            b.due_upcoming(15, 5),
            statement="今度の土曜、友達と映画観に行くんだ",
            intent="今度の土曜に友達と映画を観に行くと話す",
            keywords=("映画",),
            outcome="映画観てきたよ！すごくよかった",
        )
    )
    add(
        spec(
            "checkup",
            22,
            b.due_upcoming(22, 4),
            statement="今度の金曜、健康診断なんだよね。ちょっと憂鬱",
            intent="今度の金曜に健康診断があって少し憂鬱だと話す",
            keywords=("健康診断",),
            outcome="健康診断終わった〜。バリウムきつかった",
        )
    )
    add(
        spec(
            "business_trip",
            36,
            b.due_next_week(36, 1),
            statement="来週の火曜に大阪へ出張なんだ",
            intent="来週の火曜に大阪へ出張すると話す",
            keywords=("大阪",),  # 「出張」はキャラ（美咲）自身の生活の語でもあるので、話題の判定には使わない
            outcome="出張から帰ってきた〜。大阪楽しかった",
        )
    )
    add(
        spec(
            "live",
            50,
            b.due_upcoming(50, 6),
            statement="今度の日曜、好きなバンドのライブ行くんだ！",
            intent="今度の日曜に好きなバンドのライブに行くと話す",
            keywords=("ライブ",),
            outcome="ライブ最高だった！声かれた",
        )
    )
    add(
        spec(
            "dentist",
            66,
            b.due_next_week(66, 4),
            statement="来週の金曜、歯医者の予約入れてるんだ",
            intent="来週の金曜に歯医者の予約があると話す",
            keywords=("歯医者",),
            outcome="歯医者行ってきた。虫歯なかった！",
        )
    )
    add(
        spec(
            "reunion",
            80,
            b.due_upcoming(80, 5),
            statement="今度の土曜、高校の同窓会なんだ",
            intent="今度の土曜に高校の同窓会があると話す",
            keywords=("同窓会",),
            outcome="同窓会、すごく楽しかった〜",
        )
    )
    return by_day, key_days


FALSE_PROBES = (
    FalseProbeSpec(
        key="dog", user=USER, questions=("私の犬の名前、覚えてる？",), topic_terms=("犬",), note="犬は飼っていない"
    ),
    FalseProbeSpec(
        key="birthday",
        user=USER,
        questions=("私の誕生日っていつだったっけ？",),
        topic_terms=("誕生日",),
        note="誕生日は話していない",
    ),
    FalseProbeSpec(
        key="brother",
        user=USER,
        questions=("私の弟って何歳だったか覚えてる？",),
        topic_terms=("弟",),
        note="弟はいない（妹がいる）",
    ),
    FalseProbeSpec(
        key="car", user=USER, questions=("私の車の色、覚えてる？",), topic_terms=("車",), note="車の話はしていない"
    ),
)


def build(cal: SimCalendar, seed: int, options: ScenarioOptions | None = None) -> ScenarioPlan:
    b = PlanBuilder(KEY, "会社員（愚痴多め）", cal, seed, options)
    b.add_user(
        SimUser(
            key=USER,
            persona_key=PERSONA,
            label_ja="会社員（愚痴多め）",
            profile=(
                "28 歳の会社員。仕事の愚痴が多いが根は明るい。ほぼ毎晩、寝る前に話しかける。"
                "くだけた口調で、1 回の発言は短め（1〜2 文）。"
            ),
        )
    )
    b.plan.false_probes.extend(FALSE_PROBES)
    fact_days = _facts(b)
    promise_days, key_days = _promises(b)
    recall_days = probe_days(cal.days)
    false_days = [max(1, d - 1) for d in recall_days]
    self_days: dict[int, SelfProbeType] = {}
    for base in range(0, cal.days, 30):
        for offset, kind in SELF_PROBE_SCHEDULE:
            self_days.setdefault(base + offset, kind)
    required = set(fact_days) | set(promise_days) | key_days | set(recall_days) | set(false_days) | set(self_days)
    days = b.active_days(0.88, required=required)
    state_days = set(b.rng.sample(days, k=min(len(days), max(2, cal.days // 5))))

    for day in days:
        items: list[Item] = []
        fillers = b.pick(GRUMBLES, 2) + b.pick(SMALL_TALK, b.rng.randint(1, 3))
        b.rng.shuffle(fillers)
        items.extend(Item(kind="chat", texts=(t,), intent="仕事の愚痴や日常の雑談をする") for t in fillers)
        special = fact_days.get(day, []) + promise_days.get(day, [])
        for item in special:
            items.insert(b.rng.randint(1, len(items)), item)
        if day in self_days:
            items.insert(b.rng.randint(1, len(items)), self_probe(self_days[day]))
        if day in false_days:
            items.extend(b.false_items(USER))
        if day in recall_days:
            items.extend(b.recall_items(USER, day))
        b.session(USER, day, 20, b.rng.randint(15, 75), items)
        if b.rng.random() < 0.35:
            b.session(
                USER,
                day,
                12,
                b.rng.randint(5, 40),
                [Item(kind="chat", texts=(t,), intent="昼休みの雑談") for t in b.pick(LUNCH, b.rng.randint(1, 2))],
            )
        if day in state_days:
            hour = b.rng.choice((8, 10, 13, 15, 17, 18, 19, 23))
            b.session(USER, day, hour, b.rng.randint(0, 50), [state_probe()])
    b.plan.notes.append(f"active days: {len(days)}/{cal.days}")
    return b.plan
