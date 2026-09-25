"""構造化ログ（JSON Lines を stdout へ出力）。

1レコード = 1行の JSON。共通キー: ts / level / logger / message / request_id。
`logger.info("...", extra={"fields": {...}})` の `fields` はトップレベルに展開される。
それ以外の `extra` キーもトップレベルに出力される。
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from contextvars import ContextVar
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final
from uuid import UUID

# リクエストID（middleware で設定。バックグラウンドタスクにも引き継がれる）
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_STANDARD_ATTRS: Final[frozenset[str]] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
        "color_message",
    }
)


def json_default(value: object) -> Any:
    """json.dumps 用の既定エンコーダ（UUID / datetime / Decimal / set 等）。"""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, set | frozenset | tuple):
        return list(value)
    return str(value)


def dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=json_default, separators=(",", ":"))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            # レコード生成時に記録した request_id（非同期ハンドラでもずれない）
            "request_id": getattr(record, "request_id", None) or request_id_var.get(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key == "request_id" or key.startswith("_"):
                continue
            if key == "fields" and isinstance(value, dict):
                entry.update(value)
            else:
                entry[key] = value
        if record.exc_info:
            entry["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            entry["traceback"] = "".join(traceback.format_exception(*record.exc_info))
        if record.stack_info:
            entry["stack"] = record.stack_info
        return dumps(entry)


_record_factory_installed = False


def install_request_id_record_factory() -> None:
    """LogRecord の生成時点で request_id を記録するファクトリを（一度だけ）設定する。"""
    global _record_factory_installed  # noqa: PLW0603
    if _record_factory_installed:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        return record

    logging.setLogRecordFactory(factory)
    _record_factory_installed = True


class _JsonStdoutHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """configure_logging が設置するハンドラ（再設定時に自分の分だけ差し替えるための目印）。"""


def configure_logging(level: str = "INFO") -> None:
    """ルートロガーを JSON 出力に設定し、uvicorn のロガーもそこへ流す（何度呼んでも重複しない）。"""
    install_request_id_record_factory()
    handler = _JsonStdoutHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not isinstance(h, _JsonStdoutHandler)] + [handler]
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    # アクセスログは RequestContextMiddleware が JSON で出すため uvicorn 側は抑止
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False
    access.disabled = True
    # httpx の INFO ログ（リクエスト毎）は冗長なので WARNING 以上のみ
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"everkano.{name}")


install_request_id_record_factory()
