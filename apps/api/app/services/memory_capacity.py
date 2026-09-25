"""ユーザー × キャラあたりの記憶の上限（`MEMORY_MAX_PER_CHARACTER`）。

/chat の長期記憶検索は、ペアの全件との距離を計算する厳密検索（ADR-0005）なので、件数に比例して重くなる。
上限が無いと POST /memories の連打でペアの記憶を数万件に増やし、以後のすべての DM を遅くできてしまう。

- ユーザーの追加（POST /memories）: 上限に達していたら 422（どれを消すかはユーザーが選ぶ）
- 自動抽出・中期要約: 自動で作られた記憶のうち重要度が最も低く古いもの（ユーザー編集済み・要約を除く）を
  入れ替える。入れ替えられる記憶が無ければ、自動抽出は保存を見送る（要約は上限を超えても保存する）
- 同じペアへの同時書き込みで上限を超えないよう、トランザクション内でペア単位のアドバイザリーロックを取る
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from app.core.db import Connection
from app.services.types import MEMORY_TAG_SUMMARY

# 同じ (user_id, character_id) の記憶の追加を直列化する（トランザクション終了で自動解放）
# 1回の追加で入れ替える（削除する）自動記憶の上限。運用で上限を下げた直後などに、既存の記憶を一度に大量削除しない
# （上限を超えている分は、自動記憶が追加されるたびに少しずつ減っていく）
MAX_EVICTIONS_PER_INSERT: Final[int] = 5

_PAIR_LOCK_SQL: Final[str] = (
    "select pg_advisory_xact_lock(hashtextextended($1::uuid::text || ':' || $2::uuid::text, 0))"
)
_COUNT_SQL: Final[str] = "select count(*) from public.memories where user_id = $1 and character_id = $2"
_EVICT_SQL: Final[str] = """
delete from public.memories
 where id in (
   select id from public.memories
    where user_id = $1 and character_id = $2 and not is_user_edited and not ($3 = any(tags))
    order by importance asc, updated_at asc
    limit $4
 )
returning id, content, importance, tags
"""


@dataclass(frozen=True, slots=True)
class EvictedMemory:
    id: UUID
    content: str
    importance: float
    tags: list[str]


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
    evicted = [
        EvictedMemory(id=r["id"], content=r["content"], importance=float(r["importance"]), tags=list(r["tags"] or []))
        for r in rows
    ]
    return len(evicted) > 0, evicted
