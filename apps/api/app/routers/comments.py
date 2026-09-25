from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks

from app.container import CommentRateLimitedUser, ServicesDep
from app.models.comments import (
    CreateCommentRequest,
    CreateCommentResponse,
    GenerateCommentRequest,
    GenerateCommentResponse,
)
from app.models.common import ERROR_RESPONSES, LLM_UNAVAILABLE_RESPONSE, NOT_FOUND_RESPONSE

router = APIRouter(prefix="/comments", tags=["comments"])


@router.post(
    "",
    status_code=201,
    summary="コメントを投稿",
    description=(
        "Gate #1 でヒットした場合は 422 moderation_blocked（保存しない）。"
        "保存後、確率 COMMENT_AUTO_REPLY_PROBABILITY で投稿者キャラの返信をバックグラウンドで生成する"
        "（Realtime で届く）。"
    ),
    responses={**ERROR_RESPONSES, **NOT_FOUND_RESPONSE},
)
async def create_comment(
    body: CreateCommentRequest,
    user: CommentRateLimitedUser,
    services: ServicesDep,
    background: BackgroundTasks,
) -> CreateCommentResponse:
    return await services.comments.create(user, body, background)


@router.post(
    "/generate",
    summary="投稿者キャラがコメントに返信",
    description="出力が Gate #1 でヒットした場合は保存せず comment=null を返す。",
    responses={**ERROR_RESPONSES, **NOT_FOUND_RESPONSE, **LLM_UNAVAILABLE_RESPONSE},
)
async def generate_comment(
    body: GenerateCommentRequest, user: CommentRateLimitedUser, services: ServicesDep
) -> GenerateCommentResponse:
    return await services.comments.generate(user, body)
