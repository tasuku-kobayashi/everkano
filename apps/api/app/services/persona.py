"""ペルソナ YAML（`packages/personas/<key>.yaml`）の読込と検証（BRIEF §2.7 / 仕様 §8.1）。

- 1キャラ1ファイル。ファイル名（拡張子除く）= `key` = `characters.persona_key`。
- `age` は必須・整数・20以上（全キャラ成人）。違反するファイルがあれば起動を中止する。
- 未知のキーは無視する。
- YAML が存在しないキャラは `characters.system_prompt` から最小限のペルソナを組み立てる（フォールバック）。
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator

from app.core.logging import get_logger
from app.services.types import CharacterRecord

logger = get_logger("persona")

MIN_AGE: Final[int] = 20
DEFAULT_MODERATION_REPLY: Final[str] = "ごめん、その話はちょっとできないな"
DEFAULT_FIRST_PERSON: Final[str] = "わたし"
DEFAULT_SECOND_PERSON: Final[str] = "あなた"

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Speech(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    tone: NonEmptyStr
    sentence_length: NonEmptyStr
    emoji: NonEmptyStr
    first_person: NonEmptyStr
    second_person: NonEmptyStr
    ng_words: list[NonEmptyStr] = Field(default_factory=list)
    examples: list[NonEmptyStr] = Field(min_length=5)


class Relationship(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    initial: NonEmptyStr
    progression: NonEmptyStr


# ===========================================================================
# キャラクターエンジン v1.0 の追加項目（仕様 §10）。YAML の `engine:` セクション
#   語彙は app/engine/types.py（AFFINITY_AXES / STAGES / SEASONAL_KEYS / PROACTIVE_TRIGGERS）と一致させる。
# ===========================================================================

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
HHMM = Annotated[str, StringConstraints(pattern=r"^(?:[01]\d|2[0-4]):[0-5]\d$")]
ShortLabel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
TagStr = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,30}$")]
KeyStr = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{2,50}$")]
SeasonalKey = Literal[
    "new_year",
    "setsubun",
    "valentine",
    "white_day",
    "hanami",
    "golden_week",
    "tsuyu",
    "tanabata",
    "summer_festival",
    "obon",
    "tsukimi",
    "halloween",
    "autumn_leaves",
    "christmas",
    "year_end",
]
ProactiveTriggerName = Literal["calendar_event", "promise_due", "seasonal", "inactivity", "feed_post"]
StageName = Literal["acquaintance", "friend", "close", "lover"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AffinitySensitivity(_Frozen):
    """軸ごとの変化の感度（倍率）。possessiveness は 0 なら使わない（ヤンデレなどだけ > 0）。"""

    closeness: float = Field(default=1.0, ge=0, le=3)
    trust: float = Field(default=1.0, ge=0, le=3)
    romance: float = Field(default=1.0, ge=0, le=3)
    awkwardness: float = Field(default=1.0, ge=0, le=3)
    discontent: float = Field(default=1.0, ge=0, le=3)
    possessiveness: float = Field(default=0.0, ge=0, le=3)


class AffinityProfile(_Frozen):
    sensitivity: AffinitySensitivity
    stage_pace: float = Field(default=1.0, ge=0.3, le=3)  # 段階の上がりやすさ（大きいほど早く上がる）
    expression_delay: float = Field(default=0.0, ge=0, le=1)  # 好意を表に出す遅さ（ツンデレ: 高い）
    notes: NonEmptyStr  # 性格による動き方（評価プロンプトに渡す説明）
    # 関係の段階の上限（A7）。省略 = 上限なし（lover まで）。例: 人妻（楓）は close まで（恋人段階に進まない）。
    # 好感度エンジンはこれより上に昇格させず、既に上にいるペア（YAML を後から変えた場合）はこの段階に戻す
    max_stage: StageName | None = None


class StageStyle(_Frozen):
    """関係の段階ごとの振る舞い（A8）。call_user の {name} はユーザーの名前（記憶から分かる場合）に置き換わる。"""

    call_user: NonEmptyStr  # 例: 「{name}さん」
    call_user_fallback: NonEmptyStr  # 名前が分からないとき 例: 「きみ」
    tone: NonEmptyStr  # 口調（敬語 / タメ口 …）
    affection: NonEmptyStr  # 甘え方・好意の表し方
    topics: list[NonEmptyStr] = Field(min_length=1)
    examples: list[NonEmptyStr] = Field(min_length=2)
    proactive_frequency: float = Field(ge=0, le=3)  # 自発メッセージの頻度の倍率（段階が低いうちは控えめに P2）


class StagesProfile(_Frozen):
    acquaintance: StageStyle
    friend: StageStyle
    close: StageStyle
    lover: StageStyle


class RoutineBlock(_Frozen):
    """繰り返しの予定（C2）。end <= start なら日をまたぐ（例: 23:30〜07:00 の睡眠）。"""

    days: list[Weekday] = Field(min_length=1)
    start: HHMM
    end: HHMM
    activity: NonEmptyStr
    location: NonEmptyStr
    busyness: int = Field(ge=0, le=3)
    mood: NonEmptyStr | None = None
    status_label: ShortLabel  # UI 用（「仕事中」「おやすみ中」）
    post_tags: list[TagStr] = Field(default_factory=list)  # この予定の後に投稿するときの画像タグ
    post_probability: float = Field(default=0.0, ge=0, le=1)


class EventTemplate(_Frozen):
    """単発の出来事の候補（C3）。週ごとの確率で予定に入る。"""

    key: KeyStr
    title: NonEmptyStr
    description: NonEmptyStr | None = None
    location: NonEmptyStr
    days: list[Weekday] = Field(min_length=1)
    start: HHMM
    end: HHMM
    weekly_probability: float = Field(ge=0, le=1)
    busyness: int = Field(ge=0, le=3)
    mood: NonEmptyStr
    status_label: ShortLabel
    months: list[Annotated[int, Field(ge=1, le=12)]] | None = None  # 起こる月を限定する場合
    min_interval_days: int = Field(default=0, ge=0, le=365)
    post_tags: list[TagStr] = Field(default_factory=list)
    post_probability: float = Field(default=0.0, ge=0, le=1)


class Friend(_Frozen):
    name: NonEmptyStr
    relation: NonEmptyStr


class Place(_Frozen):
    name: NonEmptyStr
    kind: NonEmptyStr


class DefaultActivity(_Frozen):
    """予定の無い時間の過ごし方。"""

    activity: NonEmptyStr
    location: NonEmptyStr
    status_label: ShortLabel
    busyness: int = Field(default=0, ge=0, le=3)


class LifeProfile(_Frozen):
    """生活の詳細（カレンダー生成の元）。"""

    occupation: NonEmptyStr
    workplace: NonEmptyStr | None = None
    home: NonEmptyStr
    birthday: Annotated[str, StringConstraints(pattern=r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")] | None = None
    hobbies: list[NonEmptyStr] = Field(min_length=1)
    friends: list[Friend] = Field(default_factory=list)
    places: list[Place] = Field(min_length=1)
    routine: list[RoutineBlock] = Field(min_length=1)
    events: list[EventTemplate] = Field(min_length=5)
    default_activity: DefaultActivity


class SeasonalReaction(_Frozen):
    """季節・行事への反応（C4）。attends なら行事の予定をカレンダーに入れる。

    busyness / mood / status_label は行事の予定の状態（C5）。省略時はカレンダーの既定（busyness 2・
    行事ごとの気分と表示）。繁忙期の仕事（パティシエのバレンタイン・クリスマス、ネイリストの予約）のように
    「楽しい行事」ではない予定は、ここで忙しさ・気分・表示を書く。
    """

    key: SeasonalKey
    reaction: NonEmptyStr  # 行事への気持ち・過ごし方（プロンプト・自発メッセージの文脈）
    attends: bool = False
    title: NonEmptyStr | None = None  # attends のとき: 予定の名前
    location: NonEmptyStr | None = None
    start: HHMM | None = None
    end: HHMM | None = None
    busyness: int | None = Field(default=None, ge=0, le=3)  # attends のとき: 予定の忙しさ（省略 = 2）
    mood: NonEmptyStr | None = None  # attends のとき: 予定中の気分（省略 = 行事ごとの既定）
    status_label: ShortLabel | None = None  # attends のとき: UI 用の状態（省略 = 行事ごとの既定）
    post_tags: list[TagStr] = Field(default_factory=list)
    post_probability: float = Field(default=0.0, ge=0, le=1)


class ProactiveProfile(_Frozen):
    """自発メッセージの傾向（§7）。"""

    frequency: float = Field(ge=0, le=3)  # 頻度の倍率
    triggers: list[ProactiveTriggerName] = Field(min_length=1)
    style: NonEmptyStr  # どんなときに、どんな調子で送るか
    inactivity_days: int = Field(ge=1, le=30)  # 何日話さなかったら様子をうかがうか
    examples: list[NonEmptyStr] = Field(min_length=2)


class EngineProfile(_Frozen):
    affinity: AffinityProfile
    stages: StagesProfile
    life: LifeProfile
    seasonal: list[SeasonalReaction] = Field(min_length=6)
    proactive: ProactiveProfile


class Persona(BaseModel):
    """ペルソナ定義。`fallback()` で作ったもの以外は YAML から検証済み。"""

    model_config = ConfigDict(extra="ignore", frozen=True)

    key: Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{2,50}$")]
    name: NonEmptyStr
    handle: Annotated[str, StringConstraints(pattern=r"^[a-z0-9_.]{2,30}$")]
    archetype: NonEmptyStr
    age: int = Field(ge=MIN_AGE, strict=True)
    avatar: NonEmptyStr
    bio: NonEmptyStr
    profile: NonEmptyStr
    speech: Speech
    relationship: Relationship
    schedule_pattern: NonEmptyStr
    memory_focus: list[NonEmptyStr] = Field(min_length=1)
    greeting: NonEmptyStr
    comment_style: NonEmptyStr
    moderation_reply: NonEmptyStr | None = None
    # キャラクターエンジン v1.0（§10）。無い場合（フォールバック・未記入）はエンジン側の既定値で動く
    engine: EngineProfile | None = None
    is_fallback: bool = Field(default=False, exclude=True)

    @field_validator("age", mode="before")
    @classmethod
    def _age_is_int(cls, value: object) -> object:
        # bool は int のサブクラスなので明示的に弾く
        if isinstance(value, bool):
            raise ValueError("age must be an integer")
        return value

    @property
    def refusal_reply(self) -> str:
        return self.moderation_reply or DEFAULT_MODERATION_REPLY

    @classmethod
    def fallback(cls, character: CharacterRecord) -> Persona:
        """YAML が無いキャラ用。characters.system_prompt を人物設定として使う（検証はバイパス）。"""
        return cls.model_construct(
            key=character.persona_key,
            name=character.name,
            handle=character.handle,
            archetype="",
            age=MIN_AGE,
            avatar=character.avatar_url,
            bio=character.bio or "",
            profile=character.system_prompt,
            speech=Speech.model_construct(
                tone="",
                sentence_length="",
                emoji="",
                first_person=DEFAULT_FIRST_PERSON,
                second_person=DEFAULT_SECOND_PERSON,
                ng_words=[],
                examples=[],
            ),
            relationship=Relationship.model_construct(initial="", progression=""),
            schedule_pattern="",
            memory_focus=[],
            greeting=f"はじめまして、{character.name}だよ。",
            comment_style="",
            moderation_reply=None,
            engine=None,
            is_fallback=True,
        )


class PersonaLoadError(Exception):
    """ペルソナ YAML の読込・検証エラー（起動を中止する）。"""


def load_persona_file(path: Path) -> Persona:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PersonaLoadError(f"{path.name}: YAML を読み込めません: {exc}") from exc
    if not isinstance(raw, dict):
        raise PersonaLoadError(f"{path.name}: トップレベルはマッピングである必要があります")
    try:
        persona = Persona.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors(include_url=False)
        )
        raise PersonaLoadError(f"{path.name}: {details}") from exc
    if persona.key != path.stem:
        raise PersonaLoadError(f"{path.name}: key '{persona.key}' がファイル名と一致しません")
    return persona


class PersonaRepository:
    """起動時に全 YAML を読み込み、key で引けるようにする。"""

    def __init__(self, personas: Iterable[Persona]) -> None:
        self._by_key: dict[str, Persona] = {}
        for p in personas:
            self._by_key[p.key] = p

    @classmethod
    def load_dir(cls, directory: Path) -> PersonaRepository:
        files = sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])
        errors: list[str] = []
        personas: list[Persona] = []
        handles: dict[str, str] = {}
        for path in files:
            try:
                persona = load_persona_file(path)
            except PersonaLoadError as exc:
                errors.append(str(exc))
                continue
            if persona.handle in handles:
                errors.append(f"{path.name}: handle '{persona.handle}' が {handles[persona.handle]} と重複しています")
                continue
            handles[persona.handle] = path.name
            personas.append(persona)
        if errors:
            raise PersonaLoadError("ペルソナYAMLの検証に失敗しました:\n  " + "\n  ".join(errors))
        if not personas:
            logger.warning(
                "no persona YAML found; all characters fall back to characters.system_prompt",
                extra={"fields": {"personas_dir": str(directory)}},
            )
        else:
            logger.info(
                "personas loaded",
                extra={"fields": {"count": len(personas), "keys": sorted(p.key for p in personas)}},
            )
        return cls(personas)

    def get(self, key: str) -> Persona | None:
        return self._by_key.get(key)

    def for_character(self, character: CharacterRecord) -> Persona:
        persona = self._by_key.get(character.persona_key)
        if persona is None:
            logger.warning(
                "persona YAML missing; using characters.system_prompt fallback",
                extra={"fields": {"persona_key": character.persona_key, "character_id": str(character.id)}},
            )
            return Persona.fallback(character)
        return persona

    def keys(self) -> list[str]:
        return sorted(self._by_key)

    def __len__(self) -> int:
        return len(self._by_key)
