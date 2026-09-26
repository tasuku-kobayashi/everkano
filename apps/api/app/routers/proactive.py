"""自発メッセージの設定 API（エンジン v1.0 §7 / E4）。

- GET /proactive/settings: 全体の設定（有効・送らない時間帯）とキャラ別のオン・オフ
- PUT /proactive/settings: 全体の設定の更新（省略した項目は変更しない）
- PUT /proactive/settings/{character_id}: キャラ別のオン・オフ
PUT の応答は更新後の設定全体（ProactiveSettingsResponse）。変更は監査ログ `proactive.settings_update` に残す。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.container import ServicesDep
from app.core.security import CurrentUserDep
from app.engine.proactive.settings import ProactiveSettingsService
from app.models.common import ERROR_RESPONSES, NOT_FOUND_RESPONSE
from app.models.proactive import (
    ProactiveSettingsResponse,
    UpdateProactiveCharacterSettingRequest,
    UpdateProactiveGlobalSettingsRequest,
)

router = APIRouter(prefix="/proactive", tags=["proactive"])


def _service(services: ServicesDep) -> ProactiveSettingsService:
    # 送らない時間帯の既定は送信判定と同じ設定値（ENGINE_PROACTIVE_QUIET_START / END）
    return ProactiveSettingsService.from_settings(services.settings, pool=services.pool, audit=services.audit)


@router.get(
    "/settings",
    summary="自発メッセージの設定（全体 + キャラ別）",
    description=(
        "行が無い場合は既定（有効・送らない時間帯はサーバーの既定 ENGINE_PROACTIVE_QUIET_START〜END。既定 0〜7 時）。"
        "キャラ別の設定が無いキャラは有効。"
    ),
    responses=ERROR_RESPONSES,
)
async def get_proactive_settings(user: CurrentUserDep, services: ServicesDep) -> ProactiveSettingsResponse:
    return await _service(services).get(user.id)


@router.put(
    "/settings",
    summary="自発メッセージの全体設定を更新",
    description=(
        "省略した項目（または null）は変更しない。quiet_start / quiet_end は JST の時（0〜23）で、"
        "quiet_start == quiet_end なら送らない時間帯の制限なし。片方だけを指定した場合も、もう片方を今の値で"
        "埋めて両方を保存する（以後サーバーの既定が変わっても変わらない）。応答は更新後の設定全体。"
    ),
    responses=ERROR_RESPONSES,
)
async def update_proactive_settings(
    body: UpdateProactiveGlobalSettingsRequest, user: CurrentUserDep, services: ServicesDep
) -> ProactiveSettingsResponse:
    return await _service(services).update_global(user.id, body)


@router.put(
    "/settings/{character_id}",
    summary="キャラ別の自発メッセージのオン・オフ",
    description="有効なキャラだけ（それ以外は 404）。応答は更新後の設定全体。",
    responses={**ERROR_RESPONSES, **NOT_FOUND_RESPONSE},
)
async def update_proactive_character_setting(
    character_id: UUID,
    body: UpdateProactiveCharacterSettingRequest,
    user: CurrentUserDep,
    services: ServicesDep,
) -> ProactiveSettingsResponse:
    return await _service(services).update_character(user.id, character_id, body)
