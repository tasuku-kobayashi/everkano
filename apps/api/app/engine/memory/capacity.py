"""ユーザー × キャラあたりの記憶の上限（`MEMORY_MAX_PER_CHARACTER`, ADR-0024）。

記憶の検索は、ペアの全件との距離を計算する厳密検索（ADR-0005）なので、件数に比例して重くなる。
上限が無いと POST /memories の連打でペアの記憶を数万件に増やし、以後のすべての DM を遅くできてしまう。

- 数えるのは有効（status = active）な記憶だけ（検索・プロンプト・一覧の既定で使う行）。置き換えられた古い記憶
  （status = superseded。履歴として残すもの, M4）は上限の数に入れず、ペアあたり MAX_SUPERSEDED_PER_PAIR 件まで
  残して古いものから消す（`trim_superseded`。監査 memory.delete, source = history_trim）
- ユーザーの追加（POST /memories）: 上限に達していたら 422（どれを消すかはユーザーが選ぶ）
- 自動抽出・中期要約: 自動で作られた有効な記憶のうち、重要度が最も低く、更新が最も古いものを入れ替える。
  ユーザーが追加・編集した記憶と要約は入れ替えない。入れ替えられる記憶が無ければ、自動抽出は保存を見送る
  （要約は上限を超えても保存する。有効な要約の件数は summary.py が別に抑える）
- 同じペアへの同時書き込みで上限を超えないよう、トランザクション内でペア単位のアドバイザリーロックを取る
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from app.core.db import Connection
from app.services.audit import AuditLogger
from app.services.types import MEMORY_TAG_SUMMARY

# 1回の追加で入れ替える（削除する）自動記憶の上限。運用で上限を下げた直後などに、既存の記憶を一度に大量削除しない
# （上限を超えている分は、自動記憶が追加されるたびに少しずつ減っていく）
MAX_EVICTIONS_PER_INSERT: Final[int] = 5

# 同じ (user_id, character_id) の記憶の追加を直列化する（トランザクション終了で自動解放）
_PAIR_LOCK_SQL: Final[str] = (
    "select pg_advisory_xact_lock(hashtextextended($1::uuid::text || ':' || $2::uuid::text, 0))"
)
# 上限の数に入れるのは有効な記憶だけ（履歴 superseded は検索・プロンプト・一覧の既定に出ない）
_COUNT_SQL: Final[str] = (
    "select count(*) from public.memories where user_id = $1 and character_id = $2 and status = 'active'"
)
_EVICT_SQL: Final[str] = """
delete from public.memories
 where id in (
   select id from public.memories
    where user_id = $1 and character_id = $2 and status = 'active' and not is_user_edited
      and kind <> 'summary' and not ($3 = any(tags))
    order by importance asc, updated_at asc
    limit $4
 )
returning id, content, importance, tags, kind, status
"""
# 履歴（superseded）はペアあたりこの件数まで残す（新しい順。置き換えのたびに増えるので、上限とは別に抑える）
MAX_SUPERSEDED_PER_PAIR: Final[int] = 100
_TRIM_SUPERSEDED_SQL: Final[str] = """
delete from public.memories
 where id in (
   select id from public.memories
    where user_id = $1 and character_id = $2 and status = 'superseded'
    order by coalesce(superseded_at, updated_at) desc, id desc
    offset $3
 )
returning id, content, importance, tags, kind, status
"""


@dataclass(frozen=True, slots=True)
class EvictedMemory:
    id: UUID
    content: str
    importance: float
    tags: list[str]
    kind: str = "fact"
    status: str = "active"


async def lock_pair(conn: Connection, user_id: UUID, character_id: UUID) -> None:
    """ペア単位のロック（呼び出し側のトランザクション内で使う）。"""
    await conn.execute(_PAIR_LOCK_SQL, user_id, character_id)


async def count_pair(conn: Connection, user_id: UUID, character_id: UUID) -> int:
    count: int = await conn.fetchval(_COUNT_SQL, user_id, character_id)
    return count


async def make_room(
    conn: Connection, *, user_id: UUID, character_id: UUID, capacity: int
) -> tuple[bool, list[EvictedMemory]]:
    """自動で作る記憶を1件追加できるよう、必要なら自動記憶を入れ替える（トランザクション内で呼ぶ）。

    戻り値: (追加してよいか, 入れ替えで削除した記憶)。削除は呼び出し側が監査ログに残す。
    上限を大きく超えている場合も1回に削除するのは MAX_EVICTIONS_PER_INSERT 件まで
    （1件でも入れ替えられれば追加してよい）。
    """
    await lock_pair(conn, user_id, character_id)
    count = await count_pair(conn, user_id, character_id)
    if count < capacity:
        return True, []
    limit = min(count - capacity + 1, MAX_EVICTIONS_PER_INSERT)
    rows = await conn.fetch(_EVICT_SQL, user_id, character_id, MEMORY_TAG_SUMMARY, limit)
    evicted = [_evicted(r) for r in rows]
    return len(evicted) > 0, evicted


async def trim_superseded(
    conn: Connection, *, user_id: UUID, character_id: UUID, keep: int = MAX_SUPERSEDED_PER_PAIR
) -> list[EvictedMemory]:
    """履歴（superseded）を新しい順に keep 件だけ残して消す（トランザクション内で呼ぶ）。消した記憶を返す。

    置き換え（M4）のたびに履歴が 1 件増えるので、置き換えた側で呼ぶ。削除は呼び出し側が監査ログに残す
    （log_evictions(source="history_trim")）。
    """
    rows = await conn.fetch(_TRIM_SUPERSEDED_SQL, user_id, character_id, keep)
    return [_evicted(r) for r in rows]


def _evicted(row: Any) -> EvictedMemory:
    return EvictedMemory(
        id=row["id"],
        content=row["content"],
        importance=float(row["importance"]),
        tags=list(row["tags"] or []),
        kind=row["kind"],
        status=row["status"],
    )


async def log_evictions(
    audit: AuditLogger,
    evicted: list[EvictedMemory],
    *,
    user_id: UUID,
    character_id: UUID,
    capacity: int,
    now: datetime,
    source: str = "capacity_eviction",
) -> None:
    """上限による入れ替え（capacity_eviction）・履歴の整理（history_trim）で削除した自動記憶を監査ログに残す
    （ユーザーが消したものと区別できるように）。"""
    for memory in evicted:
        await audit.log(
            "memory.delete",
            user_id=user_id,
            character_id=character_id,
            payload={
                "memory_id": memory.id,
                "source": source,
                "kind": memory.kind,
                "status": memory.status,
                "content": memory.content,
                "importance": memory.importance,
                "tags": memory.tags,
                "capacity": capacity,
                "was_user_edited": False,
            },
            at=now,
        )
