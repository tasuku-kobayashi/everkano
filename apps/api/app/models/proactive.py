"""自発メッセージの設定（エンジン v1.0 §7 / E4）のスキーマ。packages/shared/src/api.ts と一致させる。"""

from __future__ import annotations

from typing import Annotated, Final
from uuid import UUID

from pydantic import ConfigDict, Field, StrictBool

from app.models.common import ApiModel

QUIET_HOUR_MIN: Final[int] = 0
QUIET_HOUR_MAX: Final[int] = 23
DEFAULT_QUIET_START: Final[int] = 0
DEFAULT_QUIET_END: Final[int] = 7

# JST の時（0〜23）。"7" のような文字列や true / 7.5 は受け付けない
QuietHour = Annotated[int, Field(strict=True, ge=QUIET_HOUR_MIN, le=QUIET_HOUR_MAX)]


class ProactiveGlobalSettings(ApiModel):
    enabled: bool = Field(description="false ならすべてのキャラから自発メッセージを受け取らない")
    quiet_start: int = Field(description="送らない時間帯の開始（JST の時, 0〜23）。start == end なら制限なし")
    quiet_end: int = Field(description="送らない時間帯の終了（JST の時, 0〜23。この時刻から送ってよい）")


class ProactiveCharacterSetting(ApiModel):
    character_id: UUID
    enabled: bool


class ProactiveSettingsResponse(ApiModel):
    """GET /proactive/settings（PUT の応答も同じ形 = 更新後の設定全体）。"""

    model_config = ConfigDict(from_attributes=True, validate_by_name=True, validate_by_alias=True)

    global_: ProactiveGlobalSettings = Field(alias="global", serialization_alias="global")
    characters: list[ProactiveCharacterSetting] = Field(
        description="キャラ別の設定（行が無いキャラは enabled = true 扱い）"
    )


class UpdateProactiveGlobalSettingsRequest(ApiModel):
    """PUT /proactive/settings — 全体設定の更新（省略した項目は変更しない。null も「変更しない」）。"""

    enabled: StrictBool | None = None
    quiet_start: QuietHour | None = None
    quiet_end: QuietHour | None = None


class UpdateProactiveCharacterSettingRequest(ApiModel):
    """PUT /proactive/settings/{character_id}"""

    enabled: StrictBool
