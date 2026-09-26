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
    # [エンジン v1.0] キャラからの自発メッセージ（返答ではない）
    is_proactive: bool
    # [エンジン v1.0] E6: 安全対応（相談窓口の案内）をしたキャラの返答。Web はこの返答の下に相談窓口のカードを出す
    safety_triggered: bool


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


class SafetyResource(ApiModel):
    """E6: 相談窓口。"""

    name: str
    phone: str | None
    hours: str | None
    url: str | None


class SafetyInfo(ApiModel):
    """E6: 自傷・希死念慮のシグナルを検知し、安全対応（相談窓口の案内）を優先した場合の情報。"""

    triggered: bool
    resources: list[SafetyResource]


class SafetyResourcesResponse(ApiModel):
    """GET /safety/resources — E6 の相談窓口の一覧（messages.safety_triggered の返答の下に出すカード用）。"""

    resources: list[SafetyResource]


class ChatResponse(ApiModel):
    message_id: UUID
    reply: str
    memories_used: list[UUID]
    # 記憶の抽出は返答の後に非同期で行うため常に空（新しい記憶は Realtime の memories INSERT で届く）
    memories_created: list[UUID]
    user_message: MessageDTO
    character_message: MessageDTO
    moderated: bool
    # [エンジン v1.0] E6 の安全対応をした場合の情報（通常は null）
    safety: SafetyInfo | None
