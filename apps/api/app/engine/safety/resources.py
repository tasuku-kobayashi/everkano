"""E6 の相談窓口と返答文面の設定（`packages/prompts/safety/resources.ja.yaml`）。

起動時に読み込んで検証する（不正なら API を起動しない）。番号・受付時間は運用者が公開前に必ず再確認する
（YAML 先頭のコメント参照）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator

from app.engine.types import SafetyResource

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
REQUIRED_MESSAGE_PLACEHOLDERS = ("{resources}",)


class SafetyConfigError(Exception):
    """相談窓口の設定ファイルの読込・検証エラー（起動を中止する）。"""


class _Resource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: NonEmpty
    phone: NonEmpty | None = None
    hours: NonEmpty | None = None
    url: Annotated[str, StringConstraints(pattern=r"^https://\S+$")] | None = None

    @field_validator("phone", mode="before")
    @classmethod
    def _phone_as_text(cls, value: object) -> object:
        # YAML で 0120-... を引用符なしで書いても文字列として扱う（数値になる書き方は拒否する）
        if isinstance(value, int | float) and not isinstance(value, bool):
            raise ValueError("phone は文字列（例: 0120-279-338）で書いてください")
        return value


class _Messages(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    casual: NonEmpty
    polite: NonEmpty

    @field_validator("casual", "polite")
    @classmethod
    def _has_placeholders(cls, value: str) -> str:
        for placeholder in REQUIRED_MESSAGE_PLACEHOLDERS:
            if placeholder not in value:
                raise ValueError(f"{placeholder} が必要です")
        return value


class SafetyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resources: list[_Resource] = Field(min_length=1)
    inline_resources: int = Field(default=2, ge=0, le=10)
    messages: _Messages

    def resource_items(self) -> tuple[SafetyResource, ...]:
        return tuple(SafetyResource(name=r.name, phone=r.phone, hours=r.hours, url=r.url) for r in self.resources)


def load_safety_config(path: Path) -> SafetyConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SafetyConfigError(f"{path}: 読み込めません: {exc}") from exc
    try:
        return SafetyConfig.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors(include_url=False)
        )
        raise SafetyConfigError(f"{path}: {details}") from exc
