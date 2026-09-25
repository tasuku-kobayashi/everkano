"""DM（会話・メッセージ・チャット）のスキーマ。"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import StringConstraints

from app.models.common import ApiModel, IsoDateTime, NoControlChars

CHAT_MESSAGE_MAX_CHARS = 2000


class ConversationDTO(ApiModel):
    id: UUID
    user_id: UUID
    character_id: UUID
    last_message_at: IsoDateTime
    user_last_read_at: IsoDateTime
    created_at: IsoDateTime


class MessageDTO(ApiModel):
    id: UUID
    conversation_id: UUID
    sender_type: Literal["user", "character"]
    body: str
    created_at: IsoDateTime


class CreateConversationRequest(ApiModel):
    character_id: UUID


class CreateConversationResponse(ApiModel):
    conversation: ConversationDTO
    created: bool
    greeting_message: MessageDTO | None


class ChatRequest(ApiModel):
    character_id: UUID
    conversation_id: UUID
    message: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=CHAT_MESSAGE_MAX_CHARS),
        NoControlChars,
    ]


class ChatResponse(ApiModel):
    message_id: UUID
    reply: str
    memories_used: list[UUID]
    memories_created: list[UUID]
    user_message: MessageDTO
    character_message: MessageDTO
    moderated: bool
