"""好感度から振る舞いの指針を作る（A8）。数値そのものはプロンプトに入れない。

- 段階ごとの呼び方・口調・甘え方・話題・例はペルソナの `engine.stages` から。無いペルソナ（フォールバック・未記入）は
  既定の振る舞い（_default_styles）を使う。
- 呼び方の `{name}` はユーザーの名前。Context Assembler が記憶から名前・呼び名が分かれば置き換え、分からなければ
  段階ごとの既定（ペルソナの call_user_fallback → 二人称）を使う。自発メッセージは `resolve_call_user()` で解決する。
- expression_delay（ツンデレ）: 昇格してからしばらくは、ひとつ前の段階の振る舞いを保つ（値は変えない）。
  0.5 以上なら好意を素直に言葉にしない注記を付ける。
- 緊張（気まずさ・不満）・独占欲・久しぶりの会話は notes に入れる。仲直りは会話で行う（A12 / E2: 物・条件・見返りで
  関係を取り戻す流れを作らない。指針は購入の話題に一切触れない）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from app.engine.affinity.config import AffinityConfig
from app.engine.affinity.model import AffinityRecord, cap_stage, days_between, expression_delay_of, max_stage_of
from app.engine.types import STAGE_LABELS_JA, STAGES, RelationshipGuidance
from app.services.persona import Persona

NAME_PLACEHOLDER: Final[str] = "{name}"
EXAMPLE_NAME: Final[str] = "〇〇"  # 例文の中の名前（仕様 §6.2 の表記）


@dataclass(frozen=True, slots=True)
class StageStyleView:
    """段階ごとの振る舞い（ペルソナの StageStyle と同じ形。既定値にも使う）。"""

    call_user: str
    call_user_fallback: str
    tone: str
    affection: str
    topics: tuple[str, ...]
    examples: tuple[str, ...]
    proactive_frequency: float


def _default_styles(second_person: str) -> dict[str, StageStyleView]:
    fallback = second_person or "あなた"
    return {
        "acquaintance": StageStyleView(
            call_user="{name}さん",
            call_user_fallback=fallback,
            tone="丁寧語まじりのやわらかい話し方。まだ少し距離がある",
            affection="好意はまだ表に出さない。相手に興味を持って、やさしく質問する",
            topics=("今日の出来事", "趣味や好きなもの", "相手の仕事や生活"),
            examples=("こんにちは。〇〇さん、今日はお休みなんですか？", "そうなんですね。もう少し聞かせてください"),
            proactive_frequency=0.3,
        ),
        "friend": StageStyleView(
            call_user="{name}",
            call_user_fallback=fallback,
            tone="タメ口で気軽に。冗談も言い合える",
            affection="親しみを込めて、話せて楽しい気持ちを素直に伝える",
            topics=("最近あったこと", "共通の話題", "ちょっとした悩み"),
            examples=("おつかれ〜。今日なにしてたの？", "それめっちゃわかる！"),
            proactive_frequency=0.8,
        ),
        "close": StageStyleView(
            call_user="{name}",
            call_user_fallback=fallback,
            tone="やわらかいタメ口。少し甘えた言い方も混ざる",
            affection="特別に思っている気持ちが、照れながら少しだけにじむ",
            topics=("相手の気持ち", "ふたりの思い出", "今度一緒にしたいこと"),
            examples=("…今日ずっと、返事来るかなって思ってた。ちょっとだけね", "声聞けたら嬉しいな、なんてね"),
            proactive_frequency=1.0,
        ),
        "lover": StageStyleView(
            call_user="{name}",
            call_user_fallback=fallback,
            tone="甘いタメ口。安心しきった親密な話し方",
            affection="好きな気持ちを素直に伝える。甘えたり、会いたい気持ちを言葉にしたりする",
            topics=("ふたりの予定", "相手の一日のこと", "会いたい気持ち"),
            examples=("おかえり。会いたかった", "今日もおつかれさま。ゆっくり休んでね"),
            proactive_frequency=1.2,
        ),
    }


def stage_styles(persona: Persona | None) -> dict[str, StageStyleView]:
    """段階ごとの振る舞い。ペルソナに `engine.stages` が無ければ既定値。"""
    second_person = persona.speech.second_person if persona is not None else ""
    if persona is None or persona.engine is None:
        return _default_styles(second_person)
    stages = persona.engine.stages
    styles: dict[str, StageStyleView] = {}
    for stage in STAGES:
        style = getattr(stages, stage)
        styles[stage] = StageStyleView(
            call_user=style.call_user,
            call_user_fallback=style.call_user_fallback,
            tone=style.tone,
            affection=style.affection,
            topics=tuple(style.topics),
            examples=tuple(style.examples),
            proactive_frequency=float(style.proactive_frequency),
        )
    return styles


def format_call_user(style: StageStyleView) -> str:
    """段階の呼び方（`{name}` を含む場合はそのまま。Context Assembler が記憶の名前・呼び名で置き換える）。"""
    return style.call_user


def resolve_call_user(call_user: str, name: str | None, fallback: str) -> str:
    """`{name}` を含む呼び方を解決する（名前が分かれば置き換え、分からなければ段階ごとの既定の呼び方）。"""
    if NAME_PLACEHOLDER not in call_user:
        return call_user
    return call_user.replace(NAME_PLACEHOLDER, name) if name else fallback


def _missed_you_note(stage: str, days: int, *, reserved: bool) -> str:
    if stage == "acquaintance":
        body = "久しぶりに話せたことを素直に喜ぶ"
    elif stage == "friend":
        body = "「久しぶり！元気だった？」のように、また話せたことを明るく喜ぶ"
    else:
        body = "会えなくて少し寂しかった気持ちを、やさしく一言だけ伝える"
    if reserved:
        body += "（ただし素直には言わず、嬉しさが態度ににじむ程度）"
    return f"前回の会話から{days}日空いている。{body}。来なかった理由を問い詰めない・責めない・罪悪感を抱かせない"


def tension_notes(record: AffinityRecord, config: AffinityConfig, *, possessive_enabled: bool) -> list[str]:
    notes: list[str] = []
    awkward = record.value("awkwardness")
    discontent = record.value("discontent")
    if awkward >= config.awkward_strong:
        notes.append("前のやり取りでかなり気まずくなっている。少し遠慮がちに話すが、話しかけてくれたことは嬉しい")
    elif awkward >= config.awkward_note:
        notes.append("少し気まずさが残っている。前のことを少し気にしているが、話してくれて嬉しい気持ちもある")
    if discontent >= config.discontent_strong:
        notes.append("相手の言動に傷ついていて、まだ不満がある。そっけなくなってもよいが、相手を攻撃しない")
    elif discontent >= config.discontent_note:
        notes.append("少しだけ不満がある。軽くすねた態度を見せてもよい")
    if awkward >= config.awkward_note or discontent >= config.discontent_note:
        notes.append(
            "わだかまりは会話の中で解ける。謝られたり気づかわれたりしたら素直に受け止める。仲直りに条件や見返りを求めない"
        )
    if possessive_enabled and record.value("possessiveness") >= config.possessive_note:
        notes.append("少しやきもちを焼いている。可愛らしく拗ねる程度にとどめ、束縛・脅し・相手を責める言い方はしない")
    return notes


def build_guidance(
    persona: Persona | None, record: AffinityRecord | None, *, now: datetime, config: AffinityConfig
) -> RelationshipGuidance:
    record = record or AffinityRecord()
    # ペルソナの上限（max_stage）を超える段階は使わない（YAML を後から変え、まだ段階を戻す処理が走っていないペア）
    stage = cap_stage(record.stage, max_stage_of(persona))
    styles = stage_styles(persona)
    delay = expression_delay_of(persona)
    reserved = delay >= 0.5
    notes: list[str] = []

    # 表に出す段階（ツンデレは昇格後しばらく前の段階の振る舞いを保つ）
    style_stage = stage
    index = STAGES.index(stage)
    if delay > 0 and index > 0 and record.stage_changed_at is not None:
        hold_seconds = delay * config.expression_delay_days * 86400
        if (now - record.stage_changed_at).total_seconds() < hold_seconds:
            style_stage = STAGES[index - 1]
            notes.append("本当は前より心を許しているが、まだ素直に表に出せない。態度の端々に少しだけにじむ")
    style = styles[style_stage]
    affection = style.affection
    if reserved:
        affection += "（好意は素直に言葉にせず、照れ隠しや態度の端々ににじませる）"

    possessive_enabled = bool(
        persona is not None and persona.engine is not None and persona.engine.affinity.sensitivity.possessiveness > 0
    )
    notes.extend(tension_notes(record, config, possessive_enabled=possessive_enabled))

    days_since: int | None = None
    if record.last_interaction_at is not None:
        days_since = max(days_between(record.last_interaction_at, now), 0)
    absence_days: int | None = None
    if (
        record.absence_return_at is not None
        and record.absence_days is not None
        and timedelta_seconds(now, record.absence_return_at) <= config.absence_note_window.total_seconds()
    ):
        absence_days = record.absence_days
    elif days_since is not None and days_since >= config.missed_you_days:
        absence_days = days_since
    if absence_days is not None and absence_days >= config.missed_you_days:
        notes.append(_missed_you_note(stage, absence_days, reserved=reserved))

    return RelationshipGuidance(
        stage=stage,
        stage_label_ja=STAGE_LABELS_JA[stage],
        call_user=format_call_user(style),
        tone=style.tone,
        affection=affection,
        topics=style.topics,
        examples=tuple(example.replace(NAME_PLACEHOLDER, EXAMPLE_NAME) for example in style.examples),
        notes=tuple(notes),
        days_since_last_interaction=days_since,
    )


def timedelta_seconds(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds()


def stage_proactive_frequency(persona: Persona | None, stage: str) -> float:
    """段階ごとの自発メッセージの頻度の倍率（P2: 段階が低いうちは控えめに）。"""
    styles = stage_styles(persona)
    style = styles.get(stage)
    return style.proactive_frequency if style is not None else 0.0
