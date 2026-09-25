from __future__ import annotations

from datetime import datetime
from typing import Final, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict

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

INVALID_CHARS_MESSAGE: Final[str] = "使用できない文字（制御文字）が含まれています。"
_ALLOWED_CONTROL_CHARS: Final[frozenset[str]] = frozenset("\n\r\t")


def reject_control_chars(value: str) -> str:
    """改行・タブ以外の制御文字（U+0000〜U+001F, U+007F）とサロゲートを拒否する。

    Postgres の text / jsonb は U+0000 を保存できず、監査ログ・LLM 呼び出しの後に 500 になるため、
    利用者が書く本文は入口（422 validation_error）で弾く。
    """
    for ch in value:
        if ch in _ALLOWED_CONTROL_CHARS:
            continue
        code = ord(ch)
        if code < 0x20 or code == 0x7F or 0xD800 <= code <= 0xDFFF:
            raise ValueError(INVALID_CHARS_MESSAGE)
    return value


# 利用者が入力する本文（メッセージ・コメント・記憶）に付ける検証
NoControlChars = AfterValidator(reject_control_chars)


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorDetail(ApiModel):
    code: ApiErrorCodeLiteral
    message: str
    request_id: str | None = None


class ApiErrorBody(ApiModel):
    """すべてのエラーレスポンスの形。message はそのまま表示できる日本語。"""

    error: ErrorDetail


_AUTH_UNAVAILABLE_DESCRIPTION = "internal_error（認証サーバー（JWKS）に一時的に接続できない。ログアウトせず再試行する）"

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ApiErrorBody, "description": "unauthorized（JWT 無効・期限切れ）"},
    403: {"model": ApiErrorBody, "description": "forbidden / account_deleted（退会済み）"},
    413: {"model": ApiErrorBody, "description": "validation_error（リクエスト本文が MAX_REQUEST_BODY_BYTES を超えた）"},
    422: {"model": ApiErrorBody, "description": "validation_error / moderation_blocked"},
    429: {"model": ApiErrorBody, "description": "rate_limited（Retry-After ヘッダ付き）"},
    500: {"model": ApiErrorBody, "description": "internal_error"},
    503: {"model": ApiErrorBody, "description": _AUTH_UNAVAILABLE_DESCRIPTION},
}
NOT_FOUND_RESPONSE: dict[int | str, dict[str, object]] = {
    404: {"model": ApiErrorBody, "description": "not_found"},
}
LLM_UNAVAILABLE_RESPONSE: dict[int | str, dict[str, object]] = {
    503: {
        "model": ApiErrorBody,
        "description": f"llm_unavailable（LLM プロバイダ障害）/ {_AUTH_UNAVAILABLE_DESCRIPTION}",
    },
}
