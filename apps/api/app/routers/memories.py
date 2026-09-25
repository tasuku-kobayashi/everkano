from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.container import ServicesDep
from app.core.security import CurrentUserDep
from app.models.common import ERROR_RESPONSES, NOT_FOUND_RESPONSE
from app.models.memories import CreateMemoryRequest, ListMemoriesResponse, MemoryDTO, UpdateMemoryRequest

router = APIRouter(prefix="/memories", tags=["memories"])


@router.get("", summary="キャラが覚えていること（自分の記憶）の一覧", responses=ERROR_RESPONSES)
async def list_memories(
    user: CurrentUserDep,
    services: ServicesDep,
    character_id: UUID = Query(description="キャラクターID"),
) -> ListMemoriesResponse:
    return await services.user_memories.list_for_character(user, character_id)


@router.post(
    "",
    status_code=201,
    summary="記憶を追加（is_user_edited = true）",
    responses={**ERROR_RESPONSES, **NOT_FOUND_RESPONSE},
)
async def create_memory(body: CreateMemoryRequest, user: CurrentUserDep, services: ServicesDep) -> MemoryDTO:
    return await services.user_memories.create(user, body)


@router.patch(
    "/{memory_id}",
    summary="記憶の内容・重要度・タグを更新（is_user_edited = true）",
    responses={**ERROR_RESPONSES, **NOT_FOUND_RESPONSE},
)
async def update_memory(
    memory_id: UUID, body: UpdateMemoryRequest, user: CurrentUserDep, services: ServicesDep
) -> MemoryDTO:
    return await services.user_memories.update(user, memory_id, body)


@router.delete(
    "/{memory_id}",
    status_code=204,
    summary="記憶を削除",
    response_class=Response,
    responses={**ERROR_RESPONSES, **NOT_FOUND_RESPONSE},
)
async def delete_memory(memory_id: UUID, user: CurrentUserDep, services: ServicesDep) -> Response:
    await services.user_memories.delete(user, memory_id)
    return Response(status_code=204)
