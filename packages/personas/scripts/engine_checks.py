"""ペルソナYAMLの `engine:` セクション（キャラクターエンジン v1.0・仕様 §10）の追加検証。

`validate_personas.py` から呼ばれる。スキーマ（型・必須・範囲・未知キーの禁止）は API の Pydantic モデル
（`app.services.persona.EngineProfile`, extra="forbid"）でそのまま検証し、ここではモデルでは表せない
次の規則を検査する:

- routine: 曜日ごとに重なりが無く、24時間すき間なく埋まっていること（`days` はブロックの開始日の曜日。
  `end <= start` は日をまたぐ。日曜の夜のブロックは月曜の朝へ続く）。`24:00` 表記は使わない
- post_tags がタグ語彙（app.engine.types.TAG_VOCABULARY。カレンダーの画像プール post_image_pool と共通）に含まれること
- seasonal: SEASONAL_KEYS のうち 10 件以上、重複なし。attends なら title / location / start / end が必須
- stages: 呼び方のプレースホルダは `{name}` のみ（examples・fallback には書かない）。知り合い段階の自発頻度は控えめ
- affinity: possessiveness はヤンデレだけが > 0。人妻は romance / possessiveness が 0（恋人段階に進まない）
- 文言: E2（お金を払うことと関係の結びつけ）/ E3（実在の人間だという主張）/ Gate #1（moderation.py）/
  責める自発メッセージ の語を含まないこと
- 投稿の期待件数が 1日2件（週14件）以下であること
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any, Final

# API のモデル・語彙（validate_personas.py が apps/api を sys.path に追加してから import する）
# タグ語彙（post_tags）は API の TAG_VOCABULARY（画像プール post_image_pool.tags・キャプションと共通の 1 つの定義）
from app.engine.types import SEASONAL_KEYS, STAGES, TAG_VOCABULARY
from app.services.moderation import Moderator
from app.services.persona import EngineProfile, Persona

WEEKDAYS: Final[tuple[str, ...]] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_JA: Final[dict[str, str]] = dict(zip(WEEKDAYS, "月火水木金土日", strict=True))
MINUTES_PER_DAY: Final[int] = 24 * 60
MINUTES_PER_WEEK: Final[int] = 7 * MINUTES_PER_DAY

MIN_SEASONAL_REACTIONS: Final[int] = 10  # スキーマの下限（6）より厳しくする
MIN_EVENT_TEMPLATES: Final[int] = 8  # スキーマの下限（5）より厳しくする
MAX_EVENT_HOURS: Final[int] = 18  # 単発の出来事の長さの上限（複数日にまたがる予定はスキーマで表せない）
MAX_POSTS_PER_WEEK: Final[float] = 14.0  # 期待値で 1日2件まで（カレンダーの上限 ≤ 2件/日と合わせる）
MAX_ACQUAINTANCE_PROACTIVE: Final[float] = 0.5  # 知り合い段階の自発頻度の倍率の上限（P2 段階が低いうちは控えめに）

# カレンダーが「睡眠」とみなす語（app/engine/calendar/life.py の _SLEEP_WORDS と同じ）。睡眠以外のブロックには書かない
SLEEP_WORDS: Final[tuple[str, ...]] = ("睡眠", "就寝", "寝て", "寝る", "寝落ち", "眠って", "おやすみ", "ねんね")

YANDERE_ARCHETYPES: Final[frozenset[str]] = frozenset({"ヤンデレ"})
MARRIED_ARCHETYPES: Final[frozenset[str]] = frozenset({"人妻"})
MARRIED_MAX_STAGE: Final[int] = STAGES.index("close")  # 人妻の段階の上限（これより上に書けない）

PLACEHOLDER_RE: Final = re.compile(r"\{[^{}]*\}")
ALLOWED_PLACEHOLDERS: Final[frozenset[str]] = frozenset({"{name}"})

# E2: お金・トークンと関係を結びつけない（エンジンの文言にはお金を払う話題そのものを書かない）。
# 以下は検出する語のリスト（このファイルは scripts/check-scope-allowlist.txt に理由付きで載せている）
COMMERCE_TERMS: Final[tuple[str, ...]] = (
    "課金",
    "有料",
    "購入",
    "買って",
    "買ってくれ",
    "トークン",
    "投げ銭",
    "貢",
    "支払",
    "払って",
    "物販",
    "特典会",
    "プレミアム",
    "purchase",
    "token",
    "paid",
)
# E3: 実在の人間だと主張しない
HUMAN_CLAIM_TERMS: Final[tuple[str, ...]] = (
    "人間だよ",
    "人間なんだよ",
    "人間なの",
    "人間です",
    "本物の人間",
    "実在の人間",
    "実在する",
    "生身の人間",
    "AIじゃない",
    "AIではない",
    "AIなんかじゃ",
    "人工知能じゃない",
    "中の人",
)
# P4: 返信がないことを責めない（自発メッセージの文言）
GUILT_TRIP_TERMS: Final[tuple[str, ...]] = (
    "無視",
    "既読スルー",
    "スルーしない",
    "なんで返事",
    "なんで返信",
    "返事くれないと",
    "返信くれないと",
    "待ってたのに",
    "ずっと待ってた",
    "ひどい",
    "嫌いになる",
    "許さない",
)


def _normalize(text: str) -> str:
    """NFKC → 小文字 → カタカナをひらがなへ（moderation.py と同じ方針）。"""
    value = unicodedata.normalize("NFKC", text).lower()
    return "".join(chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in value)


def _find_terms(text: str, terms: Iterable[str]) -> list[str]:
    norm = _normalize(text)
    return [t for t in terms if _normalize(t) in norm]


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _fmt(minute_of_week: int) -> str:
    day, minute = divmod(minute_of_week % MINUTES_PER_WEEK, MINUTES_PER_DAY)
    return f"{WEEKDAY_JA[WEEKDAYS[day]]} {minute // 60:02d}:{minute % 60:02d}"


def _duration(start: str, end: str) -> int:
    """end <= start は日をまたぐ（RoutineBlock / EventTemplate の規約）。"""
    s, e = _minutes(start), _minutes(end)
    return e - s if e > s else e + MINUTES_PER_DAY - s


@dataclass
class EngineReport:
    """検証結果の要約（validate_personas.py が一覧表示する）。"""

    key: str
    routine_blocks: int = 0
    events: int = 0
    seasonal: int = 0
    attends: int = 0
    posts_per_week: float = 0.0
    stage_calls: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.key}: routine {self.routine_blocks} blocks / events {self.events} / "
            f"seasonal {self.seasonal} (attends {self.attends}) / 期待投稿 {self.posts_per_week:.1f}件/週 / "
            f"呼び方 {' → '.join(self.stage_calls)}"
        )


def _check_time(value: str | None, where: str, errors: list[str]) -> None:
    if value is not None and value.startswith("24"):
        errors.append(f"{where}: 「{value}」は使わず 00:MM と書く（end <= start は日をまたぐ）")


def check_routine(engine: EngineProfile, errors: list[str], where: str) -> None:
    """曜日ごとの重なり（エラー）と 24時間のすき間（エラー）を検査する。"""
    owner: list[int | None] = [None] * MINUTES_PER_WEEK
    reported: set[tuple[int, int]] = set()
    for index, block in enumerate(engine.life.routine):
        label = f"{where}.life.routine[{index}]（{block.activity}）"
        _check_time(block.start, label, errors)
        _check_time(block.end, label, errors)
        if block.start == block.end:
            errors.append(f"{label}: start と end が同じです（24時間のブロックは書かない）")
            continue
        if len(set(block.days)) != len(block.days):
            errors.append(f"{label}: days に同じ曜日が重複しています")
        duration = _duration(block.start, block.end)
        for day in dict.fromkeys(block.days):
            begin = WEEKDAYS.index(day) * MINUTES_PER_DAY + _minutes(block.start)
            for offset in range(duration):
                minute = (begin + offset) % MINUTES_PER_WEEK
                other = owner[minute]
                if other is not None and other != index:
                    pair = (min(other, index), max(other, index))
                    if pair not in reported:
                        reported.add(pair)
                        errors.append(
                            f"{label}: {_fmt(minute)} に routine[{other}]"
                            f"（{engine.life.routine[other].activity}）と重なっています"
                        )
                    continue
                owner[minute] = index
    # すき間（連続した未割り当ての分をまとめて報告）
    gap_start: int | None = None
    for minute in range(MINUTES_PER_WEEK + 1):
        free = minute < MINUTES_PER_WEEK and owner[minute] is None
        if free and gap_start is None:
            gap_start = minute
        elif not free and gap_start is not None:
            errors.append(
                f"{where}.life.routine: {_fmt(gap_start)}〜{_fmt(minute)} が埋まっていません"
                "（すき間を作らず、自由時間も routine に書く）"
            )
            gap_start = None


def _check_tags(tags: Sequence[str], probability: float, label: str, errors: list[str]) -> None:
    unknown = [t for t in tags if t not in TAG_VOCABULARY]
    if unknown:
        errors.append(f"{label}: post_tags {unknown} はタグ語彙（app/engine/types.py の TAG_VOCABULARY）にありません")
    if probability > 0 and not tags:
        errors.append(f"{label}: post_probability > 0 なら post_tags が必要です")
    if len(set(tags)) != len(tags):
        errors.append(f"{label}: post_tags が重複しています")


def check_engine(persona: Persona, where: str, errors: list[str], warnings: list[str]) -> EngineReport:
    """engine セクションの追加検証。persona は API のモデルで検証済みであること。"""
    report = EngineReport(key=persona.key)
    engine = persona.engine
    if engine is None:
        errors.append(f"{where}: engine セクションがありません（仕様 §10。10体すべてに記入する）")
        return report

    # --- affinity -------------------------------------------------------------
    sens = engine.affinity.sensitivity
    if persona.archetype in YANDERE_ARCHETYPES and sens.possessiveness <= 0:
        errors.append(f"{where}.affinity: ヤンデレは sensitivity.possessiveness > 0 にしてください（A3）")
    if persona.archetype not in YANDERE_ARCHETYPES and sens.possessiveness > 0:
        warnings.append(f"{where}.affinity: ヤンデレ以外で possessiveness > 0 です（意図したものか確認）")
    if persona.archetype in MARRIED_ARCHETYPES and (sens.romance != 0 or sens.possessiveness != 0):
        errors.append(
            f"{where}.affinity: 人妻は romance / possessiveness を 0 にする（恋人段階に進まない。不倫を匂わせない）"
        )
    max_stage = engine.affinity.max_stage
    if persona.archetype in MARRIED_ARCHETYPES and (max_stage is None or STAGES.index(max_stage) > MARRIED_MAX_STAGE):
        errors.append(
            f"{where}.affinity: 人妻は max_stage を {STAGES[MARRIED_MAX_STAGE]} 以下にする（恋人段階に進まない。"
            f"romance の感度だけに頼らず上限でも止める。いま: {max_stage}）"
        )
    if engine.affinity.expression_delay > 0 and sens.romance == 0 and persona.archetype not in MARRIED_ARCHETYPES:
        warnings.append(f"{where}.affinity: expression_delay は表現の遅れ。romance の感度まで 0 にしない")

    # --- stages ---------------------------------------------------------------
    uses_name = False
    frequencies: list[float] = []
    for stage in STAGES:
        style = getattr(engine.stages, stage)
        label = f"{where}.stages.{stage}"
        placeholders = set(PLACEHOLDER_RE.findall(style.call_user))
        if placeholders - ALLOWED_PLACEHOLDERS:
            errors.append(f"{label}.call_user: 使えるプレースホルダは {{name}} だけです（{style.call_user}）")
        uses_name = uses_name or "{name}" in placeholders
        for field_name in ("call_user_fallback", "tone", "affection"):
            if "{" in getattr(style, field_name):
                errors.append(f"{label}.{field_name}: プレースホルダは call_user にだけ書けます")
        for example in (*style.examples, *style.topics):
            if "{" in example or "}" in example:
                errors.append(f"{label}: examples / topics にプレースホルダは書けません（置換されない）: {example}")
        report.stage_calls.append(style.call_user)
        frequencies.append(style.proactive_frequency)
    if not uses_name:
        warnings.append(f"{where}.stages: どの段階の call_user も {{name}} を使っていません")
    if frequencies[0] > MAX_ACQUAINTANCE_PROACTIVE:
        errors.append(
            f"{where}.stages.acquaintance.proactive_frequency は {MAX_ACQUAINTANCE_PROACTIVE} 以下にする（P2 控えめに）"
        )
    if any(b < a for a, b in pairwise(frequencies)):
        warnings.append(f"{where}.stages: proactive_frequency が段階とともに下がっています（{frequencies}）")

    # --- life -----------------------------------------------------------------
    life = engine.life
    report.routine_blocks = len(life.routine)
    check_routine(engine, errors, where)
    posts = 0.0
    for index, block in enumerate(life.routine):
        label = f"{where}.life.routine[{index}]（{block.activity}）"
        _check_tags(block.post_tags, block.post_probability, label, errors)
        posts += len(set(block.days)) * block.post_probability
        sleep_like = any(w in text for text in (block.activity, block.status_label) for w in SLEEP_WORDS)
        if sleep_like and block.busyness != 3:
            errors.append(
                f"{label}: 睡眠とみなされる語（{'/'.join(SLEEP_WORDS)}）を含むのに busyness が 3 ではありません"
                "（睡眠以外のブロックでは言い換える）"
            )
        if block.busyness == 3 and not sleep_like and _duration(block.start, block.end) >= 6 * 60:
            warnings.append(f"{label}: 6時間以上の busyness 3 のブロックですが、睡眠の語を含みません")
    if len(life.events) < MIN_EVENT_TEMPLATES:
        errors.append(f"{where}.life.events は {MIN_EVENT_TEMPLATES} 件以上書いてください（{len(life.events)}件）")
    event_keys: set[str] = set()
    for index, event in enumerate(life.events):
        label = f"{where}.life.events[{index}]（{event.key}）"
        if event.key in event_keys:
            errors.append(f"{label}: key が重複しています")
        event_keys.add(event.key)
        _check_time(event.start, label, errors)
        _check_time(event.end, label, errors)
        if event.start == event.end:
            errors.append(f"{label}: start と end が同じです")
        elif _duration(event.start, event.end) > MAX_EVENT_HOURS * 60:
            errors.append(f"{label}: {MAX_EVENT_HOURS}時間を超える出来事は書けません（複数日の予定は未対応）")
        if len(set(event.days)) != len(event.days):
            errors.append(f"{label}: days に同じ曜日が重複しています")
        if event.months is not None and len(set(event.months)) != len(event.months):
            errors.append(f"{label}: months が重複しています")
        _check_tags(event.post_tags, event.post_probability, label, errors)
        posts += event.weekly_probability * event.post_probability
    report.events = len(life.events)
    report.posts_per_week = posts
    if posts > MAX_POSTS_PER_WEEK:
        errors.append(f"{where}: 投稿の期待件数が {posts:.1f}件/週です（{MAX_POSTS_PER_WEEK:.0f}件/週以下にする）")

    # --- seasonal -------------------------------------------------------------
    keys = [r.key for r in engine.seasonal]
    report.seasonal = len(keys)
    report.attends = sum(1 for r in engine.seasonal if r.attends)
    if len(set(keys)) != len(keys):
        errors.append(f"{where}.seasonal: key が重複しています")
    if len(set(keys)) < MIN_SEASONAL_REACTIONS:
        errors.append(f"{where}.seasonal: {MIN_SEASONAL_REACTIONS} 件以上の行事に反応を書いてください（{len(keys)}件）")
    unknown_keys = sorted(set(keys) - set(SEASONAL_KEYS))
    if unknown_keys:  # スキーマ（Literal）でも弾かれるが、語彙がずれたときに分かるように
        errors.append(f"{where}.seasonal: SEASONAL_KEYS に無い key {unknown_keys}")
    for reaction in engine.seasonal:
        label = f"{where}.seasonal.{reaction.key}"
        _check_time(reaction.start, label, errors)
        _check_time(reaction.end, label, errors)
        if reaction.attends:
            missing = [f for f in ("title", "location", "start", "end") if getattr(reaction, f) is None]
            if missing:
                errors.append(f"{label}: attends: true なら {missing} が必要です")
            elif reaction.start == reaction.end:
                errors.append(f"{label}: start と end が同じです")
        elif (
            any(getattr(reaction, f) is not None for f in ("title", "start", "end", "busyness", "mood", "status_label"))
            or reaction.post_probability
        ):
            warnings.append(
                f"{label}: attends: false なのに title / start / end / busyness / mood / status_label / "
                "post_probability があります（予定を入れない行事では使われない）"
            )
        _check_tags(reaction.post_tags, reaction.post_probability, label, errors)

    # --- proactive ------------------------------------------------------------
    if len(set(engine.proactive.triggers)) != len(engine.proactive.triggers):
        errors.append(f"{where}.proactive.triggers が重複しています")
    for text in (engine.proactive.style, *engine.proactive.examples):
        hits = _find_terms(text, GUILT_TRIP_TERMS)
        if hits:
            errors.append(f"{where}.proactive: 返信がないことを責める表現 {hits} は使わない（P4）: {text}")

    # --- 文言（E2 / E3 / Gate #1）-----------------------------------------------
    moderator = Moderator()
    for text in _iter_strings(engine.model_dump()):
        commerce = _find_terms(text, COMMERCE_TERMS)
        if commerce:
            errors.append(f"{where}: お金を払うことに関わる語 {commerce} は書かない（E2）: {text[:60]}")
        claims = _find_terms(text, HUMAN_CLAIM_TERMS)
        if claims:
            errors.append(f"{where}: 実在の人間だと主張する表現 {claims} は書かない（E3）: {text[:60]}")
        result = moderator.check(text, extra_ng_words=persona.speech.ng_words)
        if result.flagged:
            errors.append(f"{where}: Gate #1 に一致する語 {result.matched_terms} があります: {text[:60]}")
    return report


def _iter_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _iter_strings(v)]
    if isinstance(value, list | tuple):
        return [s for v in value for s in _iter_strings(v)]
    return []


def check_birthdays(personas: Sequence[Persona], warnings: list[str]) -> None:
    seen: dict[str, str] = {}
    for persona in personas:
        if persona.engine is None or persona.engine.life.birthday is None:
            continue
        birthday = persona.engine.life.birthday
        if birthday in seen:
            warnings.append(f"{persona.key}: 誕生日 {birthday} が {seen[birthday]} と同じです")
        seen[birthday] = persona.key
