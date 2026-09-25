"""API エラー（ApiErrorBody = {"error": {"code", "message", "request_id"}}）。

`packages/shared/src/api.ts` の `ApiErrorCode` と HTTP ステータスの対応:
401 unauthorized / 403 forbidden・account_deleted / 404 not_found /
422 validation_error・moderation_blocked / 429 rate_limited / 503 llm_unavailable / 500 internal_error
"""

from __future__ import annotations

from typing import Any, Final, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, request_id_var

logger = get_logger("errors")

ApiErrorCode = Literal[
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

DEFAULT_MESSAGES: Final[dict[str, str]] = {
    "unauthorized": "ログインの有効期限が切れました。もう一度ログインしてください。",
    "forbidden": "この操作は許可されていません。",
    "account_deleted": "このアカウントは退会済みです。",
    "not_found": "見つかりませんでした。",
    "validation_error": "入力内容に誤りがあります。内容を確認してください。",
    "moderation_blocked": "この内容は投稿できません。表現を変えて再度お試しください。",
    "rate_limited": "送信が多すぎます。少し時間をおいてから再度お試しください。",
    "llm_unavailable": "ただいま返信できません。少し時間をおいてから再度お試しください。",
    "internal_error": "サーバーでエラーが発生しました。時間をおいて再度お試しください。",
}

_STATUS_TO_CODE: Final[dict[int, ApiErrorCode]] = {
    400: "validation_error",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "not_found",
    422: "validation_error",
    429: "rate_limited",
    503: "llm_unavailable",
}


class ApiError(Exception):
    """ハンドラで ApiErrorBody に変換される例外。"""

    def __init__(
        self,
        status_code: int,
        code: ApiErrorCode,
        message: str | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code: ApiErrorCode = code
        self.message = message or DEFAULT_MESSAGES[code]
        self.headers = headers
        super().__init__(f"{status_code} {code}: {self.message}")


def not_found(message: str | None = None) -> ApiError:
    return ApiError(404, "not_found", message)


def error_body(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "request_id": request_id_var.get()}}


def error_response(status_code: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=error_body(code, message), headers=headers)


_FIELD_LABELS: Final[dict[str, str]] = {
    "message": "メッセージ",
    "body": "コメント",
    "content": "記憶の内容",
    "importance": "重要度",
    "tags": "タグ",
    "character_id": "キャラクターID",
    "conversation_id": "会話ID",
    "post_id": "投稿ID",
    "parent_comment_id": "返信先コメントID",
}


def _validation_message(exc: RequestValidationError) -> str:
    """最初のエラーから、そのまま表示できる日本語メッセージを作る。"""
    errors = exc.errors()
    if not errors:
        return DEFAULT_MESSAGES["validation_error"]
    first = errors[0]
    loc = [str(p) for p in first.get("loc", ())]
    if loc and loc[0] in ("body", "query", "path"):
        loc = loc[1:]
    field = loc[0] if loc else ""
    label = _FIELD_LABELS.get(field, field)
    err_type = str(first.get("type", ""))
    ctx = first.get("ctx") or {}
    if err_type == "json_invalid":
        return "リクエストの形式が正しくありません。"
    if not label:
        return DEFAULT_MESSAGES["validation_error"]
    if err_type == "string_too_short":
        return f"{label}を入力してください。"
    if err_type == "string_too_long" and "max_length" in ctx:
        return f"{label}は{ctx['max_length']}文字以内で入力してください。"
    if err_type == "missing":
        return f"{label}が指定されていません。"
    if err_type == "uuid_parsing":
        return f"{label}の形式が正しくありません。"
    if err_type in ("greater_than_equal", "less_than_equal", "greater_than", "less_than"):
        return f"{label}の値が範囲外です。"
    if err_type == "value_error":
        message = str(first.get("msg", "")).removeprefix("Value error, ")
        if message:
            return message
    return f"{label}の値が正しくありません。"


async def _api_error_handler(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, ApiError):  # pragma: no cover - 登録型と一致しない場合
        raise exc
    return error_response(exc.status_code, exc.code, exc.message, exc.headers)


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):  # pragma: no cover
        raise exc
    logger.info(
        "request validation failed",
        extra={"fields": {"path": request.url.path, "errors": _safe_errors(exc)}},
    )
    return error_response(422, "validation_error", _validation_message(exc))


async def _http_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover
        raise exc
    status = exc.status_code
    code: ApiErrorCode = _STATUS_TO_CODE.get(status, "internal_error" if status >= 500 else "validation_error")
    headers = dict(exc.headers) if exc.headers else None
    return error_response(status, code, DEFAULT_MESSAGES[code], headers)


def _safe_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    # 入力値そのもの（input）はログに残さない（本文は監査ログ側で扱う）
    return [{"loc": list(e.get("loc", ())), "type": e.get("type"), "msg": e.get("msg")} for e in exc.errors()]


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
