"""約束のライフサイクル（M6 / C8）: pending → mentioned → done / cancelled。

- pending: 未達（期日前・期日の当日）
- mentioned: キャラが話題にした（自発メッセージ、または返答の分析で検出）
- done / cancelled: ユーザーが API で完了・取り消し、または返答の分析で「終わった」「中止」を検出
自動の変更（analysis / proactive）は前に進むだけ（done / cancelled から戻さない）。ユーザーの操作は
done ↔ cancelled の付け替えも許す（本人の明示的な操作を優先する）。
状態の変化はすべて監査ログ `promise.status_change` に残す（呼び出し側がコミット後に書く）。
取り消した約束に紐づくカレンダーの予定（character_events, kind = promise）は取り消し、元の記憶（「〜の予定がある」）は
履歴（superseded）にする（C8・M4。キャラが無くなった予定の話をしない。ユーザーが書いた記憶は変えない, E5）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal
from uuid import UUID

import asyncpg

from app.core.db import Connection

PromiseStatusValue = Literal["pending", "mentioned", "done", "cancelled"]
ChangeSource = Literal["analysis", "proactive", "user", "memory_deleted"]

OPEN_STATUSES: Final[frozenset[str]] = frozenset({"pending", "mentioned"})
_AUTOMATIC_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "pending": frozenset({"mentioned", "done", "cancelled"}),
    "mentioned": frozenset({"done", "cancelled"}),
    "done": frozenset(),
    "cancelled": frozenset(),
}
_USER_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "pending": frozenset({"done", "cancelled"}),
    "mentioned": frozenset({"done", "cancelled"}),
    "done": frozenset({"cancelled"}),
    "cancelled": frozenset({"done"}),
}

PROMISE_COLUMNS: Final[str] = (
    "id, user_id, character_id, content, due_at, due_precision, status, source_memory_id, source_message_id, "
    "event_id, mentioned_at, completed_at, cancelled_at, created_at, updated_at"
)

_UPDATE_STATUS_SQL: Final[str] = f"""
update public.promises
   set status = $2,
       mentioned_at = case when $2 = 'mentioned' then $3 else mentioned_at end,
       completed_at = case when $2 = 'done' then $3 when $2 = 'cancelled' then null else completed_at end,
       cancelled_at = case when $2 = 'cancelled' then $3 when $2 = 'done' then null else cancelled_at end,
       updated_at = $3
 where id = $1
returning {PROMISE_COLUMNS}
"""  # noqa: S608

# 取り消した約束の元の記憶（「〜の予定がある」）は、もう正しくないので履歴にする（ユーザーが書いた記憶は変えない, E5）
_RETIRE_SOURCE_MEMORY_SQL: Final[str] = """
update public.memories
   set status = 'superseded', superseded_at = $2, updated_at = $2
 where id = $1 and status = 'active' and not is_user_edited
returning id
"""

_CANCEL_EVENT_SQL: Final[str] = """
update public.character_events
   set status = 'cancelled'
 where id = $1 and kind = 'promise' and status = 'scheduled'
returning id
"""


def is_allowed(before: str, after: str, *, source: ChangeSource) -> bool:
    transitions = _USER_TRANSITIONS if source == "user" else _AUTOMATIC_TRANSITIONS
    return after in transitions.get(before, frozenset())


@dataclass(frozen=True, slots=True)
class StatusChange:
    promise_id: UUID
    user_id: UUID
    character_id: UUID
    before: str
    after: str
    source: ChangeSource
    event_cancelled: UUID | None
    row: asyncpg.Record
    memory_retired: UUID | None = None

    def retired_memory_payload(self, **extra: object) -> dict[str, object] | None:
        """取り消しで元の記憶を履歴にした場合の memory.supersede の payload（無ければ None）。"""
        if self.memory_retired is None:
            return None
        return {
            "old_memory_id": self.memory_retired,
            "new_memory_id": None,
            "reason": "promise_cancelled",
            "promise_id": self.promise_id,
            "source": self.source,
            **extra,
        }

    def audit_payload(self, **extra: object) -> dict[str, object]:
        return {
            "promise_id": self.promise_id,
            "before": self.before,
            "after": self.after,
            "source": self.source,
            "content": self.row["content"],
            "due_at": self.row["due_at"],
            "event_cancelled": self.event_cancelled,
            "memory_retired": self.memory_retired,
            **extra,
        }


async def change_status(
    conn: Connection,
    *,
    promise_id: UUID,
    after: PromiseStatusValue,
    now: datetime,
    source: ChangeSource,
    user_id: UUID | None = None,
) -> StatusChange | None:
    """約束の状態を変える（行をロックして遷移を検証）。変えなかったら None。トランザクション内で呼ぶ。

    user_id を渡すと所有者で絞る（他人の約束は見つからない扱い）。
    """
    row = await conn.fetchrow(
        f"select {PROMISE_COLUMNS} from public.promises where id = $1 and ($2::uuid is null or user_id = $2)"  # noqa: S608
        " for update",
        promise_id,
        user_id,
    )
    if row is None:
        return None
    before = str(row["status"])
    if before == after or not is_allowed(before, after, source=source):
        return None
    updated = await conn.fetchrow(_UPDATE_STATUS_SQL, promise_id, after, now)
    if updated is None:  # pragma: no cover - 直前にロックしている
        return None
    event_cancelled: UUID | None = None
    memory_retired: UUID | None = None
    if after == "cancelled" and updated["event_id"] is not None:
        event_cancelled = await conn.fetchval(_CANCEL_EVENT_SQL, updated["event_id"])
    if after == "cancelled" and updated["source_memory_id"] is not None:
        memory_retired = await conn.fetchval(_RETIRE_SOURCE_MEMORY_SQL, updated["source_memory_id"], now)
    return StatusChange(
        promise_id=promise_id,
        user_id=updated["user_id"],
        character_id=updated["character_id"],
        before=before,
        after=after,
        source=source,
        event_cancelled=event_cancelled,
        row=updated,
        memory_retired=memory_retired,
    )
