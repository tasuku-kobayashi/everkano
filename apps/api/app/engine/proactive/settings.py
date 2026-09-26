"""自発メッセージの設定（E4: ユーザーによる停止・送らない時間帯）の読み書き。

- すべてのクエリを検証済みの user_id でスコープする（他人の設定は読めない・書けない）。
- 全体の設定は character_id = null の行（unique nulls not distinct (user_id, character_id)）。行が無ければ既定
  （有効・送らない時間帯はサーバーの既定）。キャラ別の設定は行が無ければ有効。
- 送らない時間帯の既定は、自発メッセージの送信判定（ProactiveMessenger）と同じ設定値
  （ENGINE_PROACTIVE_QUIET_START / ENGINE_PROACTIVE_QUIET_END。既定 0〜7 時）から受け取る。
- 送らない時間帯は「開始・終了の両方が null（= サーバーの既定に従う）」か「両方が値」のどちらか
  （DB の check 制約 proactive_settings_quiet_pair）。片方だけの PUT は、もう片方を今の実効値
  （保存済みの値、無ければ既定）で埋めて両方を保存する（GET で見える値と DB の値を一致させる）。
- 変更は監査ログ `proactive.settings_update`（前後の実効値・保存した値）に残す。
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

from app.core.config import Settings
from app.core.db import Connection, Pool
from app.core.errors import not_found
from app.engine.affinity.ports import AuditLog
from app.models.proactive import (
    DEFAULT_QUIET_END,
    DEFAULT_QUIET_START,
    ProactiveSettingsResponse,
    UpdateProactiveCharacterSettingRequest,
    UpdateProactiveGlobalSettingsRequest,
)

_CHARACTER_NOT_FOUND: Final[str] = "キャラクターが見つかりません。"

_SELECT_SQL: Final[str] = """
select ps.character_id, ps.enabled, ps.quiet_start, ps.quiet_end
  from public.proactive_settings ps
  left join public.characters ch on ch.id = ps.character_id
 where ps.user_id = $1 and (ps.character_id is null or ch.is_active)
 order by ps.character_id nulls first
"""

_LOCK_GLOBAL_SQL: Final[str] = """
select enabled, quiet_start, quiet_end from public.proactive_settings
 where user_id = $1 and character_id is null
 for update
"""

_LOCK_CHARACTER_SQL: Final[str] = """
select enabled from public.proactive_settings
 where user_id = $1 and character_id = $2
 for update
"""

_UPSERT_SQL: Final[str] = """
insert into public.proactive_settings (user_id, character_id, enabled, quiet_start, quiet_end)
values ($1, $2, $3, $4, $5)
on conflict on constraint proactive_settings_user_character_key do update
   set enabled = excluded.enabled, quiet_start = excluded.quiet_start, quiet_end = excluded.quiet_end
"""

_ACTIVE_CHARACTER_SQL: Final[str] = "select 1 from public.characters where id = $1 and is_active"


class ProactiveSettingsService:
    def __init__(
        self,
        *,
        pool: Pool,
        audit: AuditLog,
        quiet_start_default: int = DEFAULT_QUIET_START,
        quiet_end_default: int = DEFAULT_QUIET_END,
    ) -> None:
        self._pool = pool
        self._audit = audit
        self._quiet_start_default = quiet_start_default
        self._quiet_end_default = quiet_end_default

    @classmethod
    def from_settings(cls, settings: Settings, *, pool: Pool, audit: AuditLog) -> ProactiveSettingsService:
        """送らない時間帯の既定を、送信判定（ProactiveMessenger）と同じ設定値から取る。"""
        return cls(
            pool=pool,
            audit=audit,
            quiet_start_default=settings.engine_proactive_quiet_start,
            quiet_end_default=settings.engine_proactive_quiet_end,
        )

    def _effective_quiet(self, start: int | None, end: int | None) -> tuple[int, int]:
        """保存値（null = 既定に従う）→ 実効値。片方だけ null の古い行も既定で補う。"""
        return (
            start if start is not None else self._quiet_start_default,
            end if end is not None else self._quiet_end_default,
        )

    async def get(self, user_id: UUID) -> ProactiveSettingsResponse:
        async with self._pool.acquire() as conn:
            return await self._read(conn, user_id)

    async def _read(self, conn: Connection, user_id: UUID) -> ProactiveSettingsResponse:
        rows = await conn.fetch(_SELECT_SQL, user_id)
        global_row = next((r for r in rows if r["character_id"] is None), None)
        quiet_start, quiet_end = self._effective_quiet(
            global_row["quiet_start"] if global_row is not None else None,
            global_row["quiet_end"] if global_row is not None else None,
        )
        return ProactiveSettingsResponse.model_validate(
            {
                "global": {
                    "enabled": bool(global_row["enabled"]) if global_row is not None else True,
                    "quiet_start": quiet_start,
                    "quiet_end": quiet_end,
                },
                "characters": [
                    {"character_id": r["character_id"], "enabled": bool(r["enabled"])}
                    for r in rows
                    if r["character_id"] is not None
                ],
            }
        )

    async def update_global(
        self, user_id: UUID, request: UpdateProactiveGlobalSettingsRequest
    ) -> ProactiveSettingsResponse:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(_LOCK_GLOBAL_SQL, user_id)
                stored_before: dict[str, Any] = {
                    "enabled": bool(row["enabled"]) if row is not None else True,
                    "quiet_start": row["quiet_start"] if row is not None else None,
                    "quiet_end": row["quiet_end"] if row is not None else None,
                }
                stored_after = dict(stored_before)
                if request.enabled is not None:
                    stored_after["enabled"] = request.enabled
                if request.quiet_start is not None or request.quiet_end is not None:
                    # 片方だけの指定でも両方を保存する（もう片方は今の実効値）。null と値の組を作らない
                    start, end = self._effective_quiet(stored_before["quiet_start"], stored_before["quiet_end"])
                    stored_after["quiet_start"] = request.quiet_start if request.quiet_start is not None else start
                    stored_after["quiet_end"] = request.quiet_end if request.quiet_end is not None else end
                elif (stored_before["quiet_start"] is None) != (stored_before["quiet_end"] is None):
                    # 制約より前の古い行（片方だけ null）は、書くときに実効値の組にそろえる
                    start, end = self._effective_quiet(stored_before["quiet_start"], stored_before["quiet_end"])
                    stored_after["quiet_start"], stored_after["quiet_end"] = start, end
                if stored_after != stored_before:
                    await conn.execute(
                        _UPSERT_SQL,
                        user_id,
                        None,
                        stored_after["enabled"],
                        stored_after["quiet_start"],
                        stored_after["quiet_end"],
                    )
            response = await self._read(conn, user_id)
        before = self._effective(stored_before)
        after = self._effective(stored_after)
        changed = sorted(k for k in after if after[k] != before[k])
        if stored_after != stored_before:
            await self._audit.log(
                "proactive.settings_update",
                user_id=user_id,
                payload={
                    "scope": "global",
                    "before": before,
                    "after": after,
                    "changed": changed,
                    # 保存した値（null = サーバーの既定に従う）
                    "stored_before": stored_before,
                    "stored_after": stored_after,
                },
            )
        return response

    def _effective(self, stored: dict[str, Any]) -> dict[str, Any]:
        start, end = self._effective_quiet(stored["quiet_start"], stored["quiet_end"])
        return {"enabled": stored["enabled"], "quiet_start": start, "quiet_end": end}

    async def update_character(
        self, user_id: UUID, character_id: UUID, request: UpdateProactiveCharacterSettingRequest
    ) -> ProactiveSettingsResponse:
        async with self._pool.acquire() as conn:
            if await conn.fetchval(_ACTIVE_CHARACTER_SQL, character_id) is None:
                raise not_found(_CHARACTER_NOT_FOUND)
            async with conn.transaction():
                row = await conn.fetchrow(_LOCK_CHARACTER_SQL, user_id, character_id)
                before = bool(row["enabled"]) if row is not None else True
                if row is None or before != request.enabled:
                    await conn.execute(_UPSERT_SQL, user_id, character_id, request.enabled, None, None)
            response = await self._read(conn, user_id)
        if before != request.enabled:
            await self._audit.log(
                "proactive.settings_update",
                user_id=user_id,
                character_id=character_id,
                payload={
                    "scope": "character",
                    "character_id": character_id,
                    "before": {"enabled": before},
                    "after": {"enabled": request.enabled},
                    "changed": ["enabled"],
                },
            )
        return response
