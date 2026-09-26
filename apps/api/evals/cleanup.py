"""評価のデータの後片付け（--keep や中断で残ったもの）。

uv run python -m evals.cleanup --run-id 1a2b3c4d     # 1 回分
uv run python -m evals.cleanup --leftovers           # 残っている評価の実行をすべて（ev_ キャラ・eval- ユーザー）
uv run python -m evals.cleanup --leftovers --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

import asyncpg

from evals.config import DEFAULT_DATABASE_URL
from evals.world import cleanup_run, leftover_run_ids


async def _main(database_url: str, run_ids: list[str], leftovers: bool, dry_run: bool) -> int:
    conn = await asyncpg.connect(database_url)
    try:
        ids = list(run_ids)
        if leftovers:
            ids += [r for r in await leftover_run_ids(conn) if r not in ids]
        if not ids:
            print("nothing to clean up")
            return 0
        for run_id in ids:
            if dry_run:
                print(f"would clean up run {run_id}")
                continue
            report = await cleanup_run(conn, run_id)
            print(f"cleaned up run {run_id}: {report.to_dict()}")
    finally:
        await conn.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.cleanup")
    parser.add_argument("--run-id", action="append", default=[])
    parser.add_argument("--leftovers", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    args = parser.parse_args(argv)
    if not args.run_id and not args.leftovers:
        parser.error("--run-id or --leftovers is required")
    return asyncio.run(_main(args.database_url, args.run_id, args.leftovers, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
