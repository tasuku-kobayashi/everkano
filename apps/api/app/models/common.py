from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

ApiErrorCodeLiteral = Literal[
    "unauthorized",
    "forbidden",
    "account_deleted",
    "not_found",
    "validation_error",
    "moderation_blocked",
    "rate_limited",
    "llm_unavailable",
    "internal_error",
]

# ISO 8601（UTC, 例: 2026-09-25T03:04:05.123456Z）で出力される日時
IsoDateTime = datetime


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorDetail(ApiModel):
    code: ApiErrorCodeLiteral
    message: str
    request_id: str | None = None


class ApiErrorBody(ApiModel):
    """すべてのエラーレスポンスの形。message はそのまま表示できる日本語。"""

    error: ErrorDetail


ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ApiErrorBody, "description": "unauthorized（JWT 無効・期限切れ）"},
    403: {"model": ApiErrorBody, "description": "forbidden / account_deleted（退会済み）"},
    422: {"model": ApiErrorBody, "description": "validation_error / moderation_blocked"},
    429: {"model": ApiErrorBody, "description": "rate_limited（Retry-After ヘッダ付き）"},
    500: {"model": ApiErrorBody, "description": "internal_error"},
}
NOT_FOUND_RESPONSE: dict[int | str, dict[str, object]] = {
    404: {"model": ApiErrorBody, "description": "not_found"},
}
LLM_UNAVAILABLE_RESPONSE: dict[int | str, dict[str, object]] = {
    503: {"model": ApiErrorBody, "description": "llm_unavailable（LLM プロバイダ障害）"},
}
