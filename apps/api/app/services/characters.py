"""キャラクター・DTO 変換などの小さな DB ヘルパー。"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Final
from uuid import UUID

import asyncpg

from app.core.db import Connection
from app.models.comments import CommentDTO
from app.models.dm import ConversationDTO, MessageDTO
from app.models.memories import MemoryDTO
from app.services.types import CharacterRecord

CHARACTER_COLUMNS: Final[str] = "id, handle, name, avatar_url, bio, persona_key, system_prompt, is_active"
CONVERSATION_COLUMNS: Final[str] = "id, user_id, character_id, last_message_at, user_last_read_at, created_at"
MESSAGE_COLUMNS: Final[str] = "id, conversation_id, sender_type, body, created_at, is_proactive, safety_triggered"
MEMORY_COLUMNS: Final[str] = (
    "id, character_id, content, importance, tags, is_user_edited, source_message_id, created_at, updated_at,"
    " kind, status, superseded_by, superseded_at, last_referenced_at, reference_count"
)
COMMENT_COLUMNS: Final[str] = (
    "id, post_id, parent_comment_id, author_type, author_user_id, author_character_id, body, created_at"
)


def character_from_row(row: asyncpg.Record) -> CharacterRecord:
    return CharacterRecord(
        id=row["id"],
        handle=row["handle"],
        name=row["name"],
        avatar_url=row["avatar_url"],
        bio=row["bio"],
        persona_key=row["persona_key"],
        system_prompt=row["system_prompt"],
        is_active=row["is_active"],
    )


async def fetch_active_character(conn: Connection, character_id: UUID) -> CharacterRecord | None:
    row = await conn.fetchrow(
        f"select {CHARACTER_COLUMNS} from public.characters where id = $1 and is_active",  # noqa: S608 - 列名は定数
        character_id,
    )
    return character_from_row(row) if row is not None else None


def to_numeric(value: float) -> Decimal:
    """numeric(3,2) 用に小数第2位で丸めた Decimal を返す。"""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def conversation_dto(row: asyncpg.Record) -> ConversationDTO:
    return ConversationDTO(
        id=row["id"],
        user_id=row["user_id"],
        character_id=row["character_id"],
        last_message_at=row["last_message_at"],
        user_last_read_at=row["user_last_read_at"],
        created_at=row["created_at"],
    )


def message_dto(row: asyncpg.Record) -> MessageDTO:
    return MessageDTO(
        id=row["id"],
        conversation_id=row["conversation_id"],
        sender_type=row["sender_type"],
        body=row["body"],
        created_at=row["created_at"],
        is_proactive=row["is_proactive"],
        safety_triggered=row["safety_triggered"],
    )


def memory_dto(row: asyncpg.Record) -> MemoryDTO:
    """memories の行（MEMORY_COLUMNS）を MemoryDTO にする。

    エンジン v1.0 の列（kind / status / superseded_* / last_referenced_at / reference_count）は、行にあれば渡す
    （MemoryDTO 側に無い項目は無視される。models/memories.py の拡張と独立に動くように model_validate を使う）。
    """
    values: dict[str, object] = dict(row.items())
    values["importance"] = float(row["importance"])
    values["tags"] = list(row["tags"] or [])
    return MemoryDTO.model_validate(values)


def comment_dto(row: asyncpg.Record) -> CommentDTO:
    return CommentDTO(
        id=row["id"],
        post_id=row["post_id"],
        parent_comment_id=row["parent_comment_id"],
        author_type=row["author_type"],
        author_user_id=row["author_user_id"],
        author_character_id=row["author_character_id"],
        body=row["body"],
        created_at=row["created_at"],
    )
