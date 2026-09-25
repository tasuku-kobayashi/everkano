from __future__ import annotations

from fastapi import APIRouter

from app.container import ServicesDep
from app.core.security import CurrentUserDep
from app.models.common import ERROR_RESPONSES, NOT_FOUND_RESPONSE
from app.models.dm import CreateConversationRequest, CreateConversationResponse

router = APIRouter(tags=["dm"])


@router.post(
    "/conversations",
    summary="会話の取得または作成",
    description="(user, character) の会話を返す。新規作成時はキャラの挨拶メッセージを1件保存して返す。",
    responses={**ERROR_RESPONSES, **NOT_FOUND_RESPONSE},
)
async def create_conversation(
    body: CreateConversationRequest, user: CurrentUserDep, services: ServicesDep
) -> CreateConversationResponse:
    return await services.conversations.get_or_create(user, body.character_id)
