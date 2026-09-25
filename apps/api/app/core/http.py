"""外部 HTTP 呼び出し（LLM / 埋め込み / JWKS）用の httpx クライアント。

- LLM・埋め込み用のクライアントは1つを共有する。`/chat` は応答生成と記憶抽出で **同時に 2 本** の
  LLM 接続を使うため、接続数の上限は 1 マシンに届く同時リクエスト数（fly.toml の
  `[http_service.concurrency] hard_limit`）の 2 倍以上にする（`tests/test_http_limits.py` で検査）。
- 接続待ち（pool）のタイムアウトは短くする。LLM のタイムアウト（30 秒）と同じだと、混雑時に接続待ちだけで
  `/chat` の締め切り（CHAT_DEADLINE_SECONDS）を使い切ってしまう。
- JWKS の取得は別クライアントにする（LLM の混雑の後ろで認証が待たされないように）。
"""

from __future__ import annotations

from typing import Final

import httpx

# 1 マシンあたりの上限（fly.toml hard_limit=80 × 2 本/チャット = 160 に余裕を持たせる）
HTTP_MAX_CONNECTIONS: Final[int] = 200
HTTP_MAX_KEEPALIVE_CONNECTIONS: Final[int] = 100
HTTP_CONNECT_TIMEOUT_SECONDS: Final[float] = 5.0
HTTP_POOL_TIMEOUT_SECONDS: Final[float] = 5.0

JWKS_TIMEOUT_SECONDS: Final[float] = 5.0
JWKS_MAX_CONNECTIONS: Final[int] = 4


def upstream_timeout(read_seconds: float) -> httpx.Timeout:
    """LLM / 埋め込み 1 回分のタイムアウト（接続・接続待ちは短く、応答の読み取りは read_seconds）。

    リクエスト単位で `timeout=` に float を渡すとクライアントの pool タイムアウトまで上書きされるため、
    呼び出し側は必ずこの Timeout を渡す。
    """
    return httpx.Timeout(
        read_seconds,
        connect=min(HTTP_CONNECT_TIMEOUT_SECONDS, read_seconds),
        pool=min(HTTP_POOL_TIMEOUT_SECONDS, read_seconds),
    )


def create_upstream_client(default_timeout_seconds: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=upstream_timeout(default_timeout_seconds),
        limits=httpx.Limits(
            max_connections=HTTP_MAX_CONNECTIONS,
            max_keepalive_connections=HTTP_MAX_KEEPALIVE_CONNECTIONS,
        ),
    )


def create_jwks_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(JWKS_TIMEOUT_SECONDS),
        limits=httpx.Limits(max_connections=JWKS_MAX_CONNECTIONS, max_keepalive_connections=JWKS_MAX_CONNECTIONS),
    )
