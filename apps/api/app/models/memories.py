"""長期メモリ（§9.4）のスキーマ。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, Field, StringConstraints, field_validator

from app.models.common import ApiModel, IsoDateTime, NoControlChars, reject_control_chars
from app.services.types import MEMORY_TAG_SUMMARY

MEMORY_CONTENT_MAX_CHARS = 500
MEMORY_TAG_MAX_CHARS = 20
MEMORY_TAGS_MAX = 10
DEFAULT_USER_MEMORY_IMPORTANCE = 0.7
# システムが付けるタグ（利用者は新たに付けられない）。summary は常にプロンプトへ注入され、重複排除の対象外になる
RESERVED_MEMORY_TAGS: frozenset[str] = frozenset({MEMORY_TAG_SUMMARY})
RESERVED_TAG_MESSAGE = f"「{MEMORY_TAG_SUMMARY}」タグは自動要約専用のため指定できません"


def _validate_tags(tags: list[str] | None) -> list[str] | None:
    if tags is None:
        return None
    cleaned: list[str] = []
    for raw in tags:
        tag = raw.strip()
        if not tag:
            raise ValueError("タグを空にすることはできません")
        if len(tag) > MEMORY_TAG_MAX_CHARS:
            raise ValueError(f"タグは{MEMORY_TAG_MAX_CHARS}文字以内で入力してください")
        reject_control_chars(tag)
        if tag not in cleaned:
            cleaned.append(tag)
    if len(cleaned) > MEMORY_TAGS_MAX:
        raise ValueError(f"タグは{MEMORY_TAGS_MAX}個までです")
    return cleaned


MemoryContent = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MEMORY_CONTENT_MAX_CHARS),
    NoControlChars,
]
Importance = Annotated[float, Field(ge=0.0, le=1.0)]
Tags = Annotated[list[str] | None, AfterValidator(_validate_tags)]


class MemoryDTO(ApiModel):
    id: UUID
    character_id: UUID
    content: str
    importance: float
    tags: list[str]
    is_user_edited: bool
    source_message_id: UUID | None
    created_at: IsoDateTime
    updated_at: IsoDateTime


class ListMemoriesResponse(ApiModel):
    memories: list[MemoryDTO]


class CreateMemoryRequest(ApiModel):
    character_id: UUID
    content: MemoryContent
    importance: Importance | None = None
    tags: Tags = None

    @field_validator("tags")
    @classmethod
    def _reject_reserved_tags(cls, tags: list[str] | None) -> list[str] | None:
        # PATCH は既存の要約の記憶に summary を残す（「秘密」の付け外し）ため、サービス側で既存タグと比べて検査する
        if tags is not None and RESERVED_MEMORY_TAGS.intersection(tags):
            raise ValueError(RESERVED_TAG_MESSAGE)
        return tags


class UpdateMemoryRequest(ApiModel):
    content: MemoryContent | None = None
    importance: Importance | None = None
    tags: Tags = None
