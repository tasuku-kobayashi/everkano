"""メモリパネル（§9.4）: ユーザーによる記憶の一覧・追加・更新・削除。

- すべてのクエリを検証済み user_id でスコープする（他人の記憶は 404）。
- 追加・更新した記憶は `is_user_edited = true`（以後、自動処理で上書きしない）。
- 記憶の本文はプロンプトに入るため Gate #1（入力）を通す。
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

from app.core.db import Pool, vector_literal
from app.core.errors import ApiError, not_found
from app.core.security import CurrentUser
from app.models.memories import (
    DEFAULT_USER_MEMORY_IMPORTANCE,
    CreateMemoryRequest,
    ListMemoriesResponse,
    MemoryDTO,
    UpdateMemoryRequest,
)
from app.services.audit import AuditLogger
from app.services.characters import MEMORY_COLUMNS, fetch_active_character, memory_dto, to_numeric
from app.services.embedding import EmbeddingClient, EmbeddingError
from app.services.moderation import Moderator

LIST_LIMIT: Final[int] = 500
_EMBEDDING_FAILED_MESSAGE: Final[str] = "記憶を保存できませんでした。少し時間をおいてから再度お試しください。"
_MEMORY_BLOCKED_MESSAGE: Final[str] = "この内容は記憶として保存できません。表現を変えて再度お試しください。"


class UserMemoryService:
    def __init__(self, *, pool: Pool, embedder: EmbeddingClient, moderator: Moderator, audit: AuditLogger) -> None:
        self._pool = pool
        self._embedder = embedder
        self._moderator = moderator
        self._audit = audit

    async def list_for_character(self, user: CurrentUser, character_id: UUID) -> ListMemoriesResponse:
        rows = await self._pool.fetch(
            f"""
            select {MEMORY_COLUMNS} from public.memories
             where user_id = $1 and character_id = $2
             order by importance desc, created_at desc
             limit $3
            """,  # noqa: S608 - 列名は定数
            user.id,
            character_id,
            LIST_LIMIT,
        )
        return ListMemoriesResponse(memories=[memory_dto(r) for r in rows])

    async def create(self, user: CurrentUser, request: CreateMemoryRequest) -> MemoryDTO:
        async with self._pool.acquire() as conn:
            character = await fetch_active_character(conn, request.character_id)
        if character is None:
            raise not_found("キャラクターが見つかりません。")
        await self._moderate(user, request.character_id, request.content)
        embedding = await self._embed(request.content)
        importance = request.importance if request.importance is not None else DEFAULT_USER_MEMORY_IMPORTANCE
        tags = request.tags or []
        row = await self._pool.fetchrow(
            f"""
            insert into public.memories
              (user_id, character_id, content, importance, tags, embedding, is_user_edited)
            values ($1, $2, $3, $4, $5, $6::text::extensions.vector, true)
            returning {MEMORY_COLUMNS}
            """,  # noqa: S608 - 列名は定数
            user.id,
            request.character_id,
            request.content,
            to_numeric(importance),
            tags,
            vector_literal(embedding),
        )
        if row is None:  # pragma: no cover
            raise RuntimeError("memory insert returned no row")
        memory = memory_dto(row)
        await self._audit.log(
            "memory.create",
            user_id=user.id,
            character_id=request.character_id,
            payload={
                "memory_id": memory.id,
                "source": "user",
                "content": memory.content,
                "importance": memory.importance,
                "tags": memory.tags,
            },
        )
        return memory

    async def update(self, user: CurrentUser, memory_id: UUID, request: UpdateMemoryRequest) -> MemoryDTO:
        if request.content is None and request.importance is None and request.tags is None:
            raise ApiError(422, "validation_error", "変更する項目を指定してください。")
        before = await self._pool.fetchrow(
            f"select {MEMORY_COLUMNS} from public.memories where id = $1 and user_id = $2",  # noqa: S608
            memory_id,
            user.id,
        )
        if before is None:
            raise not_found("記憶が見つかりません。")
        embedding_literal: str | None = None
        content_changed = request.content is not None and request.content != before["content"]
        if content_changed and request.content is not None:
            await self._moderate(user, before["character_id"], request.content)
            embedding_literal = vector_literal(await self._embed(request.content))
        row = await self._pool.fetchrow(
            f"""
            update public.memories
               set content = coalesce($3, content),
                   importance = coalesce($4, importance),
                   tags = coalesce($5, tags),
                   embedding = coalesce($6::text::extensions.vector, embedding),
                   is_user_edited = true
             where id = $1 and user_id = $2
            returning {MEMORY_COLUMNS}
            """,  # noqa: S608 - 列名は定数
            memory_id,
            user.id,
            request.content,
            to_numeric(request.importance) if request.importance is not None else None,
            request.tags,
            embedding_literal,
        )
        if row is None:
            raise not_found("記憶が見つかりません。")
        memory = memory_dto(row)
        changes: dict[str, Any] = {}
        if content_changed:
            changes["content"] = {"before": before["content"], "after": memory.content}
        if request.importance is not None:
            changes["importance"] = {"before": float(before["importance"]), "after": memory.importance}
        if request.tags is not None:
            changes["tags"] = {"before": list(before["tags"] or []), "after": memory.tags}
        await self._audit.log(
            "memory.update",
            user_id=user.id,
            character_id=memory.character_id,
            payload={"memory_id": memory.id, "source": "user", "changes": changes},
        )
        return memory

    async def delete(self, user: CurrentUser, memory_id: UUID) -> None:
        row = await self._pool.fetchrow(
            """
            delete from public.memories
             where id = $1 and user_id = $2
            returning id, character_id, content, importance, tags, is_user_edited
            """,
            memory_id,
            user.id,
        )
        if row is None:
            raise not_found("記憶が見つかりません。")
        await self._audit.log(
            "memory.delete",
            user_id=user.id,
            character_id=row["character_id"],
            payload={
                "memory_id": row["id"],
                "content": row["content"],
                "importance": float(row["importance"]),
                "tags": list(row["tags"] or []),
                "was_user_edited": row["is_user_edited"],
            },
        )

    async def _moderate(self, user: CurrentUser, character_id: UUID, text: str) -> None:
        result = self._moderator.check(text)
        if not result.flagged:
            return
        await self._audit.log(
            "moderation.flag",
            user_id=user.id,
            character_id=character_id,
            payload={
                "stage": "input",
                "context": "memory",
                "categories": result.categories,
                "matched_terms": result.matched_terms,
                "text": text,
            },
        )
        raise ApiError(422, "moderation_blocked", _MEMORY_BLOCKED_MESSAGE)

    async def _embed(self, text: str) -> list[float]:
        try:
            [vector] = await self._embedder.embed([text])
        except EmbeddingError as exc:
            raise ApiError(503, "llm_unavailable", _EMBEDDING_FAILED_MESSAGE) from exc
        return vector
