"""ペルソナ YAML（`packages/personas/<key>.yaml`）の読込と検証（BRIEF §2.7 / 仕様 §8.1）。

- 1キャラ1ファイル。ファイル名（拡張子除く）= `key` = `characters.persona_key`。
- `age` は必須・整数・20以上（全キャラ成人）。違反するファイルがあれば起動を中止する。
- 未知のキーは無視する。
- YAML が存在しないキャラは `characters.system_prompt` から最小限のペルソナを組み立てる（フォールバック）。
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Final

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
