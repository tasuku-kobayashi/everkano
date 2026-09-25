"""コメントのスキーマ。"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import StringConstraints

from app.models.common import ApiModel, IsoDateTime, NoControlChars

COMMENT_BODY_MAX_CHARS = 500


class CommentDTO(ApiModel):
    id: UUID
    post_id: UUID
    parent_comment_id: UUID | None
    author_type: Literal["user", "character"]
    author_user_id: UUID | None
    author_character_id: UUID | None
    body: str
    created_at: IsoDateTime


class CreateCommentRequest(ApiModel):
    post_id: UUID
    body: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=COMMENT_BODY_MAX_CHARS),
        NoControlChars,
    ]
    parent_comment_id: UUID | None = None


class CreateCommentResponse(ApiModel):
    comment: CommentDTO
    reply_scheduled: bool


class GenerateCommentRequest(ApiModel):
    post_id: UUID
    parent_comment_id: UUID


class GenerateCommentResponse(ApiModel):
    comment: CommentDTO | None
