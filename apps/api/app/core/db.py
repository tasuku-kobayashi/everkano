"""asyncpg 接続プール。

API は `postgres` ロール（RLS バイパス）で接続するため、**すべてのクエリで
検証済み user_id によるスコープ（所有者チェック）を行うこと**（BRIEF §2.1）。

pgvector の `vector` 型は `extensions` スキーマにある。ベクトルはテキストリテラル
`'[0.1,0.2,...]'` として渡し、SQL 側で `$n::text::extensions.vector` にキャストする。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import asyncpg
from asyncpg.pool import PoolConnectionProxy

from app.core.config import Settings
from app.core.logging import json_default

# asyncpg のクラスは実行時には総称型ではないため、PEP 695 の遅延評価型エイリアスで定義する
type Pool = asyncpg.Pool[asyncpg.Record]
# 直接接続とプールから借りた接続（プロキシ）のどちらも受け付ける
type Connection = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]

# ベクトル演算子は search_path に依存しないよう完全修飾で使う
VECTOR_COSINE_DISTANCE = "operator(extensions.<=>)"


def _encode_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=json_default)


async def _init_connection(conn: asyncpg.Connection[asyncpg.Record]) -> None:
    await conn.set_type_codec("jsonb", encoder=_encode_json, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=_encode_json, decoder=json.loads, schema="pg_catalog")


async def create_pool(settings: Settings) -> Pool:
    pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=settings.database_pool_min_size,
        max_size=settings.database_pool_max_size,
        statement_cache_size=settings.database_statement_cache_size,
        init=_init_connection,
        command_timeout=30,
        server_settings={"application_name": "everkano-api"},
    )
    if pool is None:  # pragma: no cover - asyncpg の型定義上 Optional
        raise RuntimeError("failed to create database pool")
    return pool


def vector_literal(values: Sequence[float]) -> str:
    """pgvector のテキスト表現（`[0.1,0.2,...]`）に変換する。"""
    return "[" + ",".join(format(float(v), ".7g") for v in values) + "]"


async def check_database(pool: Pool, timeout_seconds: float = 2.0) -> bool:
    try:
        async with pool.acquire(timeout=timeout_seconds) as conn:
            value = await conn.fetchval("select 1", timeout=timeout_seconds)
    except (OSError, TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError):
        return False
    return bool(value == 1)
