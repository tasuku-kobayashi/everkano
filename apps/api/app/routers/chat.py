from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks

from app.container import ChatRateLimitedUser, ServicesDep
from app.models.common import ERROR_RESPONSES, LLM_UNAVAILABLE_RESPONSE, NOT_FOUND_RESPONSE
from app.models.dm import ChatRequest, ChatResponse

router = APIRouter(tags=["dm"])


@router.post(
    "/chat",
    summary="DM のキャラ返答を生成",
    description=(
        "ユーザー発言を Gate #1 で検証し、短期・長期メモリとペルソナからプロンプトを組み立てて返答を生成する。"
        "ユーザー発言とキャラ返答を保存し、重要な記憶を抽出して保存する。"
    ),
    responses={**ERROR_RESPONSES, **NOT_FOUND_RESPONSE, **LLM_UNAVAILABLE_RESPONSE},
)
async def chat(
    body: ChatRequest, user: ChatRateLimitedUser, services: ServicesDep, background: BackgroundTasks
) -> ChatResponse:
    return await services.chat.chat(user, body, background)
