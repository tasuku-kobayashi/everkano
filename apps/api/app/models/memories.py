"""長期メモリ（§9.4）のスキーマ。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, Field, StringConstraints

from app.models.common import ApiModel, IsoDateTime

MEMORY_CONTENT_MAX_CHARS = 500
MEMORY_TAG_MAX_CHARS = 20
MEMORY_TAGS_MAX = 10
DEFAULT_USER_MEMORY_IMPORTANCE = 0.7


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
        if tag not in cleaned:
            cleaned.append(tag)
    if len(cleaned) > MEMORY_TAGS_MAX:
        raise ValueError(f"タグは{MEMORY_TAGS_MAX}個までです")
    return cleaned


MemoryContent = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MEMORY_CONTENT_MAX_CHARS)
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


class UpdateMemoryRequest(ApiModel):
    content: MemoryContent | None = None
    importance: Importance | None = None
    tags: Tags = None
