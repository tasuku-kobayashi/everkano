"""記憶の embedding を、現在の埋め込み設定で計算し直す（運用スクリプト `scripts/reembed_memories.py` 用）。

`EMBEDDING_MODE`（hash ↔ live）や `EMBEDDING_MODEL` を切り替えると、既存の記憶の embedding は
古いベクトル空間のまま残る。次元数はどちらも 1536 なのでエラーにはならないが、新しい設定で作った
問い合わせベクトルとの類似度は意味を持たなくなる（検索がほぼランダムになり、重複排除も効かない）。
切り替え直後にこの処理を実行する。

- 対象: `memories`（ユーザーについての記憶）と `character_memories`（キャラ側の記憶, M8 / C9）。
- `memory_tombstones`（削除した記憶の墓標）は本文を持たないため計算し直せない。切り替え後の墓標は
  本文のハッシュ（完全一致）でだけ復活を防ぐ（類似度による判定は、切り替え後に削除したものから効く）。
- 何度実行しても結果は同じ（冪等）。id 順にバッチで処理する。
- 取得後に本文が変わった行（同時に編集された記憶）は上書きしない（本文一致を条件に更新）。
- `memories.updated_at` は変えない（埋め込みだけの更新。マイグレーション 20260926100000_memory.sql のトリガー）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal
from uuid import UUID

from app.core.db import Pool, vector_literal
from app.engine.memory.embedding import EmbeddingClient

ReembedTable = Literal["memories", "character_memories"]

# テーブル名は定数のみを埋め込む（利用者入力は含まない）
_SELECT_SQL: Final[dict[ReembedTable, str]] = {
    "memories": """
select id, content from public.memories
 where ($1::uuid is null or user_id = $1)
   and ($2::uuid is null or id > $2)
 order by id
 limit $3
""",
    "character_memories": """
select id, content from public.character_memories
 where ($1::uuid is null or user_id = $1)
   and ($2::uuid is null or id > $2)
 order by id
 limit $3
""",
}

_UPDATE_SQL: Final[dict[ReembedTable, str]] = {
    "memories": """
update public.memories m
   set embedding = u.embedding::extensions.vector
  from unnest($1::uuid[], $2::text[], $3::text[]) as u(id, content, embedding)
 where m.id = u.id and m.content = u.content
""",
    "character_memories": """
update public.character_memories m
   set embedding = u.embedding::extensions.vector
  from unnest($1::uuid[], $2::text[], $3::text[]) as u(id, content, embedding)
 where m.id = u.id and m.content = u.content
""",
}


@dataclass(slots=True)
class ReembedStats:
    scanned: int = 0
    updated: int = 0
    batches: int = 0


async def _reembed_table(
    table: ReembedTable,
    pool: Pool,
    embedder: EmbeddingClient,
    *,
    batch_size: int,
    dry_run: bool,
    user_id: UUID | None,
    on_batch: Callable[[ReembedStats], None] | None,
) -> ReembedStats:
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    stats = ReembedStats()
    last_id: UUID | None = None
    while True:
        rows = await pool.fetch(_SELECT_SQL[table], user_id, last_id, batch_size)
        if not rows:
            break
        last_id = rows[-1]["id"]
        stats.scanned += len(rows)
        stats.batches += 1
        if not dry_run:
            vectors = await embedder.embed([r["content"] for r in rows])
            status = await pool.execute(
                _UPDATE_SQL[table],
                [r["id"] for r in rows],
                [r["content"] for r in rows],
                [vector_literal(v) for v in vectors],
            )
            stats.updated += int(status.split()[-1])
        if on_batch is not None:
            on_batch(stats)
    return stats


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
    return await _reembed_table(
        "memories", pool, embedder, batch_size=batch_size, dry_run=dry_run, user_id=user_id, on_batch=on_batch
    )


async def reembed_character_memories(
    pool: Pool,
    embedder: EmbeddingClient,
    *,
    batch_size: int = 100,
    dry_run: bool = False,
    user_id: UUID | None = None,
    on_batch: Callable[[ReembedStats], None] | None = None,
) -> ReembedStats:
    """character_memories の embedding を計算し直す。user_id を指定するとそのユーザーとの会話由来の分だけ処理する
    （全ユーザー共通の記憶 user_id = null は user_id 未指定のときだけ対象になる）。"""
    return await _reembed_table(
        "character_memories",
        pool,
        embedder,
        batch_size=batch_size,
        dry_run=dry_run,
        user_id=user_id,
        on_batch=on_batch,
    )
