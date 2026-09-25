"""長期メモリの embedding を、現在の埋め込み設定で計算し直す（運用スクリプト用）。

`EMBEDDING_MODE`（hash ↔ live）や `EMBEDDING_MODEL` を切り替えると、既存の記憶の embedding は
古いベクトル空間のまま残る。次元数はどちらも 1536 なのでエラーにはならないが、新しい設定で作った
問い合わせベクトルとの類似度は意味を持たなくなる（検索がほぼランダムになり、重複排除も効かない）。
切り替え直後に `scripts/reembed_memories.py` からこの処理を実行する。

- 何度実行しても結果は同じ（冪等）。id 順にバッチで処理する。
- 取得後に本文が変わった行（同時に編集された記憶）は上書きしない（本文一致を条件に更新）。
- `memories.updated_at` はトリガーで実行時刻に更新される。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from app.core.db import Pool, vector_literal
from app.services.embedding import EmbeddingClient

_SELECT_SQL: Final[str] = """
select id, content from public.memories
 where ($1::uuid is null or user_id = $1)
   and ($2::uuid is null or id > $2)
 order by id
 limit $3
"""

_UPDATE_SQL: Final[str] = """
update public.memories m
   set embedding = u.embedding::extensions.vector
  from unnest($1::uuid[], $2::text[], $3::text[]) as u(id, content, embedding)
 where m.id = u.id and m.content = u.content
"""


@dataclass(slots=True)
class ReembedStats:
    scanned: int = 0
    updated: int = 0
    batches: int = 0


async def reembed_memories(
    pool: Pool,
    embedder: EmbeddingClient,
    *,
    batch_size: int = 100,
    dry_run: bool = False,
    user_id: UUID | None = None,
    on_batch: Callable[[ReembedStats], None] | None = None,
) -> ReembedStats:
    """memories の embedding を `embedder` で計算し直す。user_id を指定するとそのユーザー分だけ処理する。"""
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    stats = ReembedStats()
    last_id: UUID | None = None
    while True:
        rows = await pool.fetch(_SELECT_SQL, user_id, last_id, batch_size)
        if not rows:
            break
        last_id = rows[-1]["id"]
        stats.scanned += len(rows)
        stats.batches += 1
        if not dry_run:
            vectors = await embedder.embed([r["content"] for r in rows])
            status = await pool.execute(
                _UPDATE_SQL,
                [r["id"] for r in rows],
                [r["content"] for r in rows],
                [vector_literal(v) for v in vectors],
            )
            stats.updated += int(status.split()[-1])
        if on_batch is not None:
            on_batch(stats)
    return stats
