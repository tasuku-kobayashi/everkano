from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.container import MemoryWriteRateLimitedUser, ServicesDep
from app.core.security import CurrentUserDep
from app.models.common import ERROR_RESPONSES, NOT_FOUND_RESPONSE
from app.models.memories import CreateMemoryRequest, ListMemoriesResponse, MemoryDTO, UpdateMemoryRequest

router = APIRouter(prefix="/memories", tags=["memories"])


@router.get(
    "",
    summary="キャラが覚えていること（自分の記憶）の一覧",
    description=(
        "既定は有効な記憶だけ。include_superseded=true で、新しい情報に置き換えられた古い記憶（履歴, M4）も返す。"
    ),
    responses=ERROR_RESPONSES,
)
async def list_memories(
    user: CurrentUserDep,
    services: ServicesDep,
    character_id: UUID = Query(description="キャラクターID"),
    include_superseded: bool = Query(default=False, description="置き換えられた古い記憶（履歴）も含める"),
) -> ListMemoriesResponse:
    return await services.user_memories.list_for_character(user, character_id, include_superseded=include_superseded)


@router.post(
    "",
    status_code=201,
    summary="記憶を追加（is_user_edited = true）",
    description=(
        "ユーザー × キャラの記憶が MEMORY_MAX_PER_CHARACTER 件に達している場合は 422 validation_error。"
        "`summary` タグ・種類（自動要約専用）は指定できない。kind の省略時は fact。"
        "POST / PATCH は RATE_LIMIT_MEMORIES_PER_MINUTE で制限する。"
    ),
    responses={**ERROR_RESPONSES, **NOT_FOUND_RESPONSE},
)
async def create_memory(
    body: CreateMemoryRequest, user: MemoryWriteRateLimitedUser, services: ServicesDep
) -> MemoryDTO:
    return await services.user_memories.create(user, body)


@router.patch(
    "/{memory_id}",
    summary="記憶の内容・重要度・タグ・種類を更新（is_user_edited = true）",
    description=(
        "`summary` タグは、もともと要約の記憶にだけ残せる（新たに付けることはできない）。"
        "種類に summary は指定できず、要約の記憶の種類は変えられない。"
        "以後、自動処理はこの記憶を上書き・置き換えしない（E5）。"
    ),
    responses={**ERROR_RESPONSES, **NOT_FOUND_RESPONSE},
)
async def update_memory(
    memory_id: UUID, body: UpdateMemoryRequest, user: MemoryWriteRateLimitedUser, services: ServicesDep
) -> MemoryDTO:
    return await services.user_memories.update(user, memory_id, body)


@router.delete(
    "/{memory_id}",
    status_code=204,
    summary="記憶を削除",
    description=(
        "記憶の行を削除し、本文を持たない墓標（本文のハッシュと埋め込み）を残す。自動抽出は同じ・よく似た記憶を"
        "作り直さない（E5）。この記憶から作られた未達の約束は取り消す。"
    ),
    response_class=Response,
    responses={**ERROR_RESPONSES, **NOT_FOUND_RESPONSE},
)
async def delete_memory(memory_id: UUID, user: CurrentUserDep, services: ServicesDep) -> Response:
    await services.user_memories.delete(user, memory_id)
    return Response(status_code=204)
