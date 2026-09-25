"""Sentry（任意・SENTRY_DSN 設定時のみ）の初期化と送信内容のスクラブ。

`send_default_pii=False` だけでは次のものが Sentry に送られてしまうため、明示的に止める:

- スタックフレームのローカル変数（ASGI の scope に `authorization: Bearer <JWT>` が bytes のまま入っている）
  → `include_local_variables=False` + `before_send` で `vars` を削除
- リクエスト本文（DM の本文・記憶の内容）→ `max_request_body_size="never"` + `before_send` で `request.data` を削除
- ログのパンくず（監査ログの複製 = 発言・返答・プロンプト全文）→ ログはパンくずにしない（`level=None`）、
  監査ロガーは Sentry から完全に除外（`ignore_logger`）
- ERROR ログのイベント化は残す（障害の検知に必要）。ただし `extra` の本文系キー（payload / text / body 等）は伏せる

送るのは例外の型・メッセージ・スタックトレース（変数なし）・リクエストのメソッドと URL・許可したヘッダーだけ。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Final

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger
from sentry_sdk.transport import Transport

from app import __version__
from app.core.config import Settings
from app.core.logging import AUDIT_LOGGER_NAME

REDACTED: Final[str] = "[redacted]"

# Sentry に残してよいリクエストヘッダー（それ以外は送らない。Authorization / Cookie は論外）
_ALLOWED_HEADERS: Final[frozenset[str]] = frozenset(
    {"user-agent", "content-type", "content-length", "x-request-id", "origin", "accept"}
)
# extra / contexts の中で値を伏せるキー（利用者の本文・プロンプト・トークンが入りうるもの）
_SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "payload",
        "text",
        "body",
        "message",
        "reply",
        "content",
        "output",
        "prompt_messages",
        "messages",
        "raw_output",
        "candidates",
        "memories",
        "authorization",
        "cookie",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        # プロバイダのエラー本文（モデレーションで弾かれた入力の一部を含むことがある）や例外の詳細。
        # 詳細は stdout のログ（request_id で突合）で見る
        "error",
        "detail",
    }
)
# パンくずとして残してよいカテゴリ（外部 HTTP・DB クエリ。どちらも本文やパラメータは含まない）
_ALLOWED_BREADCRUMB_CATEGORIES: Final[frozenset[str]] = frozenset({"httplib", "http", "query", "redis"})


def _redact(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return REDACTED
    if isinstance(value, Mapping):
        return {
            key: (REDACTED if str(key).lower() in _SENSITIVE_KEYS else _redact(item, depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_redact(item, depth + 1) for item in value]
    return value


def scrub_event(event: dict[str, Any], _hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """Sentry に送る前のイベントから、トークン・本文・ローカル変数を取り除く（before_send）。"""
    request = event.get("request")
    if isinstance(request, dict):
        for key in ("data", "cookies", "env"):
            request.pop(key, None)
        headers = request.get("headers")
        if isinstance(headers, Mapping):
            request["headers"] = {k: v for k, v in headers.items() if str(k).lower() in _ALLOWED_HEADERS}
        else:
            request.pop("headers", None)
    for container in ("exception", "threads"):
        values = (event.get(container) or {}).get("values") or []
        for item in values:
            frames = ((item or {}).get("stacktrace") or {}).get("frames") or []
            for frame in frames:
                if isinstance(frame, dict):
                    frame.pop("vars", None)
    for key in ("extra", "contexts"):
        if isinstance(event.get(key), Mapping):
            event[key] = _redact(event[key])
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        # ログの引数（% 書式の値）は送らない（メッセージの雛形だけ残す）
        logentry.pop("params", None)
    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, dict):
        values = [b for b in (breadcrumbs.get("values") or []) if scrub_breadcrumb(b, None) is not None]
        breadcrumbs["values"] = values
    event.pop("user", None)
    return event


def scrub_breadcrumb(crumb: dict[str, Any], _hint: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """外部 HTTP と DB クエリ以外のパンくず（ログ等）は捨てる（before_breadcrumb）。"""
    if crumb.get("category") not in _ALLOWED_BREADCRUMB_CATEGORIES:
        return None
    data = crumb.get("data")
    if isinstance(data, Mapping):
        crumb["data"] = _redact(data)
    return crumb


def init_sentry(settings: Settings, *, transport: type[Transport] | None = None) -> bool:
    """SENTRY_DSN が設定されていれば Sentry を初期化する。初期化したら True。"""
    if not settings.sentry_dsn:
        return False
    options: dict[str, Any] = {}
    if transport is not None:
        options["transport"] = transport
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        release=f"everkano-api@{__version__}",
        send_default_pii=False,
        traces_sample_rate=0.0,
        include_local_variables=False,
        max_request_body_size="never",
        integrations=[LoggingIntegration(level=None, event_level=logging.ERROR)],
        before_send=scrub_event,  # type: ignore[arg-type]
        before_breadcrumb=scrub_breadcrumb,
        **options,
    )
    # 監査ログ（発言・返答・プロンプト全文を含む）は Sentry に一切流さない（stdout と audit_logs のみ）
    ignore_logger(AUDIT_LOGGER_NAME)
    return True
