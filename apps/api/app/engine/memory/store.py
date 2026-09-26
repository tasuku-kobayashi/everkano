"""メモリエンジンの SQL（検索・重複判定・墓標・約束・キャラ側の記憶）。

- すべて (user_id, character_id) で絞る（M9: キャラAに話したことをキャラBは知らない）。
- 類似度の検索は MATERIALIZED CTE でペアの全件の距離を計算する厳密検索（ADR-0005。絞り込み付きの HNSW は
  該当行を取りこぼすため使わない）。件数は MEMORY_MAX_PER_CHARACTER で抑えている（ADR-0024）。
- 演算子名は定数のみを埋め込む（利用者入力は含まない）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID

import asyncpg

from app.core.db import VECTOR_COSINE_DISTANCE, Connection
from app.engine.memory.ranking import Candidate

_MEMORY_FIELDS: Final[str] = "id, kind, content, importance, tags, created_at, last_referenced_at, is_user_edited"

CANDIDATES_SQL: Final[str] = f"""
with pair as materialized (
  select {_MEMORY_FIELDS},
         embedding {VECTOR_COSINE_DISTANCE} $3::text::extensions.vector as distance
    from public.memories
   where user_id = $1 and character_id = $2 and status = 'active' and embedding is not null
)
select {_MEMORY_FIELDS}, 1 - distance as similarity
  from pair
 order by distance asc
 limit $4
"""  # noqa: S608

# 埋め込みが無い（検索用の埋め込みに失敗した）ときの候補: 重要度と新しさの順
FALLBACK_CANDIDATES_SQL: Final[str] = f"""
select {_MEMORY_FIELDS}, null::float8 as similarity
  from public.memories
 where user_id = $1 and character_id = $2 and status = 'active' and kind <> 'summary'
 order by importance desc, greatest(created_at, coalesce(last_referenced_at, created_at)) desc
 limit $3
"""  # noqa: S608

# 常に入れる記憶: 呼び方・距離感（relationship）/ 最新の要約 / 重要度の高い事実（仕事・住まいなど）
PINNED_SQL: Final[str] = f"""
(select 1 as slot, {_MEMORY_FIELDS}, null::float8 as similarity
   from public.memories
  where user_id = $1 and character_id = $2 and status = 'active' and kind = 'relationship'
  order by created_at desc
  limit $3)
union all
(select 2 as slot, {_MEMORY_FIELDS}, null::float8 as similarity
   from public.memories
  where user_id = $1 and character_id = $2 and status = 'active' and kind = 'summary'
  order by created_at desc
  limit $4)
union all
(select 3 as slot, {_MEMORY_FIELDS}, null::float8 as similarity
   from public.memories
  where user_id = $1 and character_id = $2 and status = 'active' and kind = 'fact' and importance >= $5
  order by importance desc, created_at desc
  limit $6)
"""  # noqa: S608

DUE_PROMISES_SQL: Final[str] = """
select id, content, due_at, due_precision, status, source_memory_id
  from public.promises
 where user_id = $1 and character_id = $2 and status in ('pending', 'mentioned')
   and due_at >= $3 and due_at <= $4
 order by due_at asc, created_at asc
 limit $5
"""

CHARACTER_MEMORIES_SQL: Final[str] = f"""
with cm as materialized (
  select id, kind, content, occurred_at, user_id, created_at, embedding
    from public.character_memories
   where character_id = $1
     and (user_id = $2 or (user_id is null and coalesce(occurred_at, created_at) >= $3))
     and coalesce(occurred_at, created_at) <= $4
   order by created_at desc
   limit 200
)
select id, kind, content, occurred_at, user_id, created_at,
       case when $5::text is null or embedding is null then null
            else 1 - (embedding {VECTOR_COSINE_DISTANCE} $5::text::extensions.vector) end as similarity
  from cm
"""  # noqa: S608

# 分析に添える既存の記憶: ユーザーの文ごとの近い記憶（上位 k 件）
RELATED_SQL: Final[str] = f"""
with pair as materialized (
  select id, kind, content, importance, is_user_edited, created_at, embedding
    from public.memories
   where user_id = $1 and character_id = $2 and status = 'active' and kind <> 'summary' and embedding is not null
),
q as (
  select u.vec::extensions.vector as vec, u.i
    from unnest($3::text[]) with ordinality as u(vec, i)
),
scored as (
  select p.id, p.kind, p.content, p.importance, p.is_user_edited, p.created_at,
         1 - (p.embedding {VECTOR_COSINE_DISTANCE} q.vec) as similarity,
         row_number() over (partition by q.i order by p.embedding {VECTOR_COSINE_DISTANCE} q.vec) as rn
    from pair p cross join q
)
select id, kind, content, importance, is_user_edited, created_at, max(similarity) as similarity
  from scored
 where rn <= $4
 group by id, kind, content, importance, is_user_edited, created_at
"""  # noqa: S608

# 分析に常に添える記憶（矛盾の検出用）: 事実・関係性・好み
BASE_MEMORIES_SQL: Final[str] = """
select id, kind, content, importance, is_user_edited, created_at, null::float8 as similarity
  from public.memories
 where user_id = $1 and character_id = $2 and status = 'active' and kind in ('fact', 'relationship', 'preference')
 order by importance desc, created_at desc
 limit $3
"""

OPEN_PROMISES_SQL: Final[str] = """
select id, content, due_at, due_precision, status, event_id
  from public.promises
 where user_id = $1 and character_id = $2 and status in ('pending', 'mentioned')
 order by due_at asc nulls last, created_at asc
 limit $3
"""

NEAREST_ACTIVE_SQL: Final[str] = f"""
with pair as materialized (
  select id, kind, content, importance, is_user_edited,
         embedding {VECTOR_COSINE_DISTANCE} $3::text::extensions.vector as distance
    from public.memories
   where user_id = $1 and character_id = $2 and status = 'active' and kind <> 'summary' and embedding is not null
)
select id, kind, content, importance, is_user_edited, 1 - distance as similarity
  from pair
 order by distance asc
 limit 1
"""  # noqa: S608

NEAREST_TOMBSTONE_SQL: Final[str] = f"""
with pair as materialized (
  select id, content_hash,
         case when embedding is null then null
              else 1 - (embedding {VECTOR_COSINE_DISTANCE} $3::text::extensions.vector) end as similarity
    from public.memory_tombstones
   where user_id = $1 and character_id = $2
)
select id, content_hash = $4 as same_hash, similarity
  from pair
 order by (content_hash = $4) desc, similarity desc nulls last
 limit 1
"""  # noqa: S608

NEAREST_STATEMENT_SQL: Final[str] = f"""
with pair as materialized (
  select id, embedding {VECTOR_COSINE_DISTANCE} $3::text::extensions.vector as distance
    from public.character_memories
   where character_id = $1 and user_id = $2 and embedding is not null
)
select id, 1 - distance as similarity from pair order by distance asc limit 1
"""  # noqa: S608

INSERT_MEMORY_SQL: Final[str] = """
insert into public.memories
  (user_id, character_id, kind, content, importance, tags, embedding, source_message_id, source_conversation_id,
   is_user_edited, created_at, updated_at)
values ($1, $2, $3, $4, $5, $6, $7::text::extensions.vector, $8, $9, false, $10, $10)
returning id
"""

INSERT_PROMISE_SQL: Final[str] = """
insert into public.promises
  (user_id, character_id, content, due_at, due_precision, status, source_memory_id, source_message_id,
   created_at, updated_at)
values ($1, $2, $3, $4, $5, 'pending', $6, $7, $8, $8)
returning id
"""

INSERT_CHARACTER_MEMORY_SQL: Final[str] = """
insert into public.character_memories
  (character_id, user_id, kind, content, occurred_at, source_message_id, embedding, created_at)
values ($1, $2, 'self_statement', $3, $4, $5, $6::text::extensions.vector, $7)
returning id
"""


def to_float(value: Any) -> float:
    return float(value) if value is not None else 0.0


def candidate_from_row(row: asyncpg.Record) -> Candidate:
    similarity = row["similarity"]
    return Candidate(
        id=row["id"],
        kind=row["kind"],
        content=row["content"],
        importance=to_float(row["importance"]),
        tags=tuple(row["tags"] or ()),
        created_at=row["created_at"],
        last_referenced_at=row["last_referenced_at"],
        is_user_edited=row["is_user_edited"],
        similarity=float(similarity) if similarity is not None else None,
    )


@dataclass(frozen=True, slots=True)
class NearMemory:
    id: UUID
    kind: str
    content: str
    importance: float
    is_user_edited: bool
    similarity: float


async def nearest_active(conn: Connection, user_id: UUID, character_id: UUID, literal: str) -> NearMemory | None:
    row = await conn.fetchrow(NEAREST_ACTIVE_SQL, user_id, character_id, literal)
    if row is None:
        return None
    return NearMemory(
        id=row["id"],
        kind=row["kind"],
        content=row["content"],
        importance=to_float(row["importance"]),
        is_user_edited=row["is_user_edited"],
        similarity=to_float(row["similarity"]),
    )


@dataclass(frozen=True, slots=True)
class TombstoneMatch:
    id: UUID
    same_hash: bool
    similarity: float | None


async def nearest_tombstone(
    conn: Connection, user_id: UUID, character_id: UUID, literal: str, content_hash: str
) -> TombstoneMatch | None:
    row = await conn.fetchrow(NEAREST_TOMBSTONE_SQL, user_id, character_id, literal, content_hash)
    if row is None:
        return None
    similarity = row["similarity"]
    return TombstoneMatch(
        id=row["id"], same_hash=bool(row["same_hash"]), similarity=float(similarity) if similarity is not None else None
    )


async def fetch_character_rows(
    conn: Connection,
    *,
    character_id: UUID,
    user_id: UUID,
    shared_since: datetime,
    now: datetime,
    literal: str | None,
) -> Sequence[asyncpg.Record]:
    return await conn.fetch(CHARACTER_MEMORIES_SQL, character_id, user_id, shared_since, now, literal)
