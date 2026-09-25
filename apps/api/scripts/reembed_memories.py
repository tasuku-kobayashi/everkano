"""長期メモリ（memories.embedding）を、現在の EMBEDDING_* 設定で計算し直す。

EMBEDDING_MODE を hash → live に切り替えたとき（または EMBEDDING_MODEL を変えたとき）は、デプロイ直後に必ず実行する。
実行するまでの間、既存の記憶の検索（長期メモリの再注入）と重複排除は正しく動かない。

使い方（apps/api で。環境変数 / apps/api/.env の DATABASE_URL・EMBEDDING_* を使う）:
  uv run python scripts/reembed_memories.py [--dry-run] [--batch-size 100] [--user-id <uuid>]
Fly.io:
  fly ssh console --config apps/api/fly.toml -C "python scripts/reembed_memories.py"

- --dry-run   : 対象件数を数えるだけで更新しない
- --user-id   : 指定ユーザーの記憶だけを処理する（動作確認用）
何度実行しても結果は同じ（冪等）。memories.updated_at は実行時刻に更新される。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

import httpx

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.db import create_pool  # noqa: E402
from app.services.embedding import create_embedding_client  # noqa: E402
from app.services.reembed import ReembedStats, reembed_memories  # noqa: E402


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="memories.embedding を現在の埋め込み設定で再計算する")
    parser.add_argument("--dry-run", action="store_true", help="件数を数えるだけで更新しない")
    parser.add_argument("--batch-size", type=int, default=100, help="1回の埋め込み API 呼び出しで扱う件数")
    parser.add_argument("--user-id", type=UUID, default=None, help="このユーザーの記憶だけを処理する")
    return parser.parse_args(argv)


def _progress(stats: ReembedStats) -> None:
    print(f"  batch {stats.batches}: scanned={stats.scanned} updated={stats.updated}", flush=True)


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    print(
        f"embedding: mode={settings.embedding_mode} model={settings.embedding_model} "
        f"dims={settings.embedding_dimensions} dry_run={args.dry_run}"
    )
    pool = await create_pool(settings)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(settings.llm_timeout_seconds)) as http:
            embedder = create_embedding_client(settings, http)
            stats = await reembed_memories(
                pool,
                embedder,
                batch_size=args.batch_size,
                dry_run=args.dry_run,
                user_id=args.user_id,
                on_batch=_progress,
            )
    finally:
        await pool.close()
    print(f"done: scanned={stats.scanned} updated={stats.updated} batches={stats.batches}")
    return 0


def main() -> int:
    return asyncio.run(_run(_parse_args(sys.argv[1:])))


if __name__ == "__main__":
    raise SystemExit(main())
