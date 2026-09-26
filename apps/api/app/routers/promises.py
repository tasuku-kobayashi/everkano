"""約束（エンジン v1.0 §4 M6 / §5 C8）: メモリパネルの約束の一覧と、ユーザーによる完了・取り消し。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from app.container import ServicesDep
from app.core.security import CurrentUserDep
from app.models.common import ERROR_RESPONSES, NOT_FOUND_RESPONSE
from app.models.promises import ListPromisesResponse, PromiseDTO, UpdatePromiseRequest

router = APIRouter(prefix="/promises", tags=["promises"])


@router.get(
    "",
    summary="キャラとの約束・予定の一覧（自分の約束）",
    description=(
        "既定は未達（pending / mentioned）だけを期日の近い順に返す。include_closed=true で完了・取り消し済みも返す。"
    ),
    responses=ERROR_RESPONSES,
)
async def list_promises(
    user: CurrentUserDep,
    services: ServicesDep,
    character_id: UUID = Query(description="キャラクターID"),
    include_closed: bool = Query(default=False, description="完了・取り消し済みの約束も含める"),
) -> ListPromisesResponse:
    return await services.user_memories.list_promises(user, character_id, include_closed=include_closed)


@router.patch(
    "/{promise_id}",
    summary="約束を完了・取り消しにする",
    description=(
        "status は done（完了）/ cancelled（取り消し）。同じ状態への変更は何もせずに現在の約束を返す。"
        "取り消した約束のカレンダーの予定も取り消す。状態の変化は監査ログ promise.status_change に残す。"
    ),
    responses={**ERROR_RESPONSES, **NOT_FOUND_RESPONSE},
)
async def update_promise(
    promise_id: UUID, body: UpdatePromiseRequest, user: CurrentUserDep, services: ServicesDep
) -> PromiseDTO:
    return await services.user_memories.update_promise(user, promise_id, body)
