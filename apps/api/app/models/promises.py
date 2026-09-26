"""約束（エンジン v1.0 §4 M6 / §5 C8）のスキーマ。packages/shared/src/api.ts と一致させる。"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from app.models.common import ApiModel, IsoDateTime

PromiseStatus = Literal["pending", "mentioned", "done", "cancelled"]
DuePrecision = Literal["datetime", "day", "week", "month", "unknown"]


class PromiseDTO(ApiModel):
    id: UUID
    character_id: UUID
    content: str
    due_at: IsoDateTime | None
    due_precision: DuePrecision
    # pending = 未達 / mentioned = キャラが話題にした / done = 完了 / cancelled = 取り消し
    status: PromiseStatus
    created_at: IsoDateTime
    updated_at: IsoDateTime


class ListPromisesResponse(ApiModel):
    promises: list[PromiseDTO]


class UpdatePromiseRequest(ApiModel):
    """ユーザーによる完了・取り消し。"""

    status: Literal["done", "cancelled"]
