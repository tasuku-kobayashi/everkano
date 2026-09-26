"""メモリパネル（M11 / §9.4）: ユーザーによる記憶の一覧・追加・更新・削除と、約束の一覧・完了・取り消し。

- すべてのクエリを検証済み user_id でスコープする（他人の記憶・約束は 404）。
- 追加・更新した記憶は `is_user_edited = true`（以後、自動処理で上書き・置き換えしない。E5）。
- 削除した記憶は内容を持たない「墓標」（memory_tombstones: 本文のハッシュと埋め込み）を残して行を消す。
  自動抽出は墓標と同じ・よく似た記憶を作らない（E5）。ユーザー自身が追加し直すのは可。
  削除した記憶から作られた未達の約束は取り消す（キャラが消した話題を持ち出さない）。
- 記憶の本文はプロンプトに入るため Gate #1（入力）を通す。
- ユーザー × キャラの記憶は `MEMORY_MAX_PER_CHARACTER` 件まで（超える追加は 422。capacity.py）。
- `summary`（自動要約専用）のタグ・種類は新たに付けられない。
- 埋め込みは `USER_MEMORY_EMBED_DEADLINE_SECONDS` で打ち切り 503（Web の 15 秒のタイムアウトより前に返し、
  クライアントの再送で同じ記憶が二重に保存されることを防ぐ）。
- 作成・更新・削除の日時はアプリの時計（Clock）で書く（評価ハーネスの時間の早送り）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Final, Literal
from uuid import UUID

from app.core.config import Settings
from app.core.db import Pool, vector_literal
from app.core.errors import ApiError, not_found
from app.core.logging import get_logger
from app.core.security import CurrentUser
from app.engine.memory.capacity import count_pair, lock_pair
from app.engine.memory.embedding import EmbeddingClient, EmbeddingError, embedding_failure_payload
from app.engine.memory.promises import OPEN_STATUSES, PROMISE_COLUMNS, change_status
from app.engine.memory.text import content_hash
from app.engine.types import Clock, SystemClock
from app.models.memories import (
    DEFAULT_USER_MEMORY_IMPORTANCE,
    DEFAULT_USER_MEMORY_KIND,
    RESERVED_MEMORY_TAGS,
    RESERVED_TAG_MESSAGE,
    CreateMemoryRequest,
    ListMemoriesResponse,
    MemoryDTO,
    UpdateMemoryRequest,
)
from app.models.promises import ListPromisesResponse, PromiseDTO, UpdatePromiseRequest
from app.services.audit import AuditLogger
from app.services.characters import MEMORY_COLUMNS, fetch_active_character, memory_dto, to_numeric
from app.services.moderation import Moderator

logger = get_logger("memory.panel")

# Web の既定タイムアウト（DEFAULT_TIMEOUT_MS = 15 秒）より短くする
USER_MEMORY_EMBED_DEADLINE_SECONDS: Final[float] = 10.0
PROMISES_LIST_LIMIT: Final[int] = 200
_EMBEDDING_FAILED_MESSAGE: Final[str] = "記憶を保存できませんでした。しばらくしてから再度お試しください。"
_MEMORY_BLOCKED_MESSAGE: Final[str] = "この内容は記憶として保存できません。表現を変えて再度お試しください。"
_SUMMARY_KIND_LOCKED_MESSAGE: Final[str] = "会話の要約の種類は変更できません"


def promise_dto(row: Any) -> PromiseDTO:
    return PromiseDTO(
        id=row["id"],
        character_id=row["character_id"],
        content=row["content"],
        due_at=row["due_at"],
        due_precision=row["due_precision"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class UserMemoryService:
    def __init__(
        self,
        *,
        settings: Settings,
        pool: Pool,
        embedder: EmbeddingClient,
        moderator: Moderator,
        audit: AuditLogger,
        embed_deadline_seconds: float = USER_MEMORY_EMBED_DEADLINE_SECONDS,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings
        self._pool = pool
        self._embedder = embedder
        self._moderator = moderator
        self._audit = audit
        self._embed_deadline = embed_deadline_seconds
        self._clock: Clock = clock or SystemClock()

    # ================================================================== 記憶
    async def list_for_character(
        self, user: CurrentUser, character_id: UUID, *, include_superseded: bool = False
    ) -> ListMemoriesResponse:
        # 上限（MEMORY_MAX_PER_CHARACTER）までの全件を返す。要約は上限を超えて保存されることがあるので少し余裕を持たせる
        rows = await self._pool.fetch(
            f"""
            select {MEMORY_COLUMNS} from public.memories
             where user_id = $1 and character_id = $2 and ($3 or status = 'active')
             order by (status = 'active') desc, importance desc, created_at desc
             limit $4
            """,  # noqa: S608 - 列名は定数
            user.id,
            character_id,
            include_superseded,
            self._settings.memory_max_per_character + 50,
        )
        return ListMemoriesResponse(memories=[memory_dto(r) for r in rows])

    async def create(self, user: CurrentUser, request: CreateMemoryRequest) -> MemoryDTO:
        capacity = self._settings.memory_max_per_character
        async with self._pool.acquire() as conn:
            character = await fetch_active_character(conn, request.character_id)
            if character is None:
                raise not_found("キャラクターが見つかりません。")
            # 上限に達しているなら、埋め込み（有料 API）を呼ぶ前に断る
            if await count_pair(conn, user.id, request.character_id) >= capacity:
                raise _capacity_error(capacity)
        await self._moderate(user, request.character_id, request.content)
        embedding = await self._embed(request.content, user=user, character_id=request.character_id, action="create")
        importance = request.importance if request.importance is not None else DEFAULT_USER_MEMORY_IMPORTANCE
        kind = request.kind or DEFAULT_USER_MEMORY_KIND
        tags = request.tags or []
        now = self._clock.now()
        async with self._pool.acquire() as conn, conn.transaction():
            # 同時に追加されても上限を超えないよう、ペア単位でロックしてから数え直す
            await lock_pair(conn, user.id, request.character_id)
            if await count_pair(conn, user.id, request.character_id) >= capacity:
                raise _capacity_error(capacity)
            row = await conn.fetchrow(
                f"""
                insert into public.memories
                  (user_id, character_id, kind, content, importance, tags, embedding, is_user_edited,
                   created_at, updated_at)
                values ($1, $2, $3, $4, $5, $6, $7::text::extensions.vector, true, $8, $8)
                returning {MEMORY_COLUMNS}
                """,  # noqa: S608 - 列名は定数
                user.id,
                request.character_id,
                kind,
                request.content,
                to_numeric(importance),
                tags,
                vector_literal(embedding),
                now,
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
                "kind": memory.kind,
                "content": memory.content,
                "importance": memory.importance,
                "tags": memory.tags,
            },
            at=now,
        )
        return memory

    async def update(self, user: CurrentUser, memory_id: UUID, request: UpdateMemoryRequest) -> MemoryDTO:
        if request.content is None and request.importance is None and request.tags is None and request.kind is None:
            raise ApiError(422, "validation_error", "変更する項目を指定してください。")
        before = await self._pool.fetchrow(
            f"select {MEMORY_COLUMNS} from public.memories where id = $1 and user_id = $2",  # noqa: S608
            memory_id,
            user.id,
        )
        if before is None:
            raise not_found("記憶が見つかりません。")
        if request.tags is not None and RESERVED_MEMORY_TAGS.intersection(request.tags).difference(
            before["tags"] or []
        ):
            # 要約の記憶に元から付いている summary は残せる（「秘密」の付け外しで送り返される）が、新たには付けられない
            raise ApiError(422, "validation_error", RESERVED_TAG_MESSAGE)
        if request.kind is not None and before["kind"] == "summary" and request.kind != "summary":
            raise ApiError(422, "validation_error", _SUMMARY_KIND_LOCKED_MESSAGE)
        embedding_literal: str | None = None
        content_changed = request.content is not None and request.content != before["content"]
        if content_changed and request.content is not None:
            await self._moderate(user, before["character_id"], request.content)
            vector = await self._embed(
                request.content, user=user, character_id=before["character_id"], action="update", memory_id=memory_id
            )
            embedding_literal = vector_literal(vector)
        now = self._clock.now()
        row = await self._pool.fetchrow(
            f"""
            update public.memories
               set content = coalesce($3, content),
                   importance = coalesce($4, importance),
                   tags = coalesce($5, tags),
                   embedding = coalesce($6::text::extensions.vector, embedding),
                   kind = coalesce($7, kind),
                   is_user_edited = true,
                   updated_at = $8
             where id = $1 and user_id = $2
            returning {MEMORY_COLUMNS}
            """,  # noqa: S608 - 列名は定数
            memory_id,
            user.id,
            request.content,
            to_numeric(request.importance) if request.importance is not None else None,
            request.tags,
            embedding_literal,
            request.kind,
            now,
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
        if request.kind is not None:
            changes["kind"] = {"before": before["kind"], "after": memory.kind}
        await self._audit.log(
            "memory.update",
            user_id=user.id,
            character_id=memory.character_id,
            payload={"memory_id": memory.id, "source": "user", "changes": changes},
            at=now,
        )
        return memory

    async def delete(self, user: CurrentUser, memory_id: UUID) -> None:
        """記憶を削除し、墓標を残す（E5: 自動抽出で復活させない）。"""
        now = self._clock.now()
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                select id, character_id, kind, content, importance, tags, is_user_edited, status,
                       embedding::text as embedding
                  from public.memories
                 where id = $1 and user_id = $2
                 for update
                """,
                memory_id,
                user.id,
            )
            if row is None:
                raise not_found("記憶が見つかりません。")
            # この記憶から作られた未達の約束は取り消す（外部キーは on delete set null なので、削除の前に探す）
            promise_ids = await conn.fetch(
                "select id from public.promises where source_memory_id = $1 and user_id = $2 and status = any($3)",
                memory_id,
                user.id,
                sorted(OPEN_STATUSES),
            )
            cancelled = []
            for promise in promise_ids:
                change = await change_status(
                    conn, promise_id=promise["id"], after="cancelled", now=now, source="memory_deleted", user_id=user.id
                )
                if change is not None:
                    cancelled.append(change)
            digest = content_hash(row["content"])
            tombstone_id: UUID = await conn.fetchval(
                """
                insert into public.memory_tombstones (user_id, character_id, kind, content_hash, embedding, deleted_at)
                values ($1, $2, $3, $4, $5::text::extensions.vector, $6)
                returning id
                """,
                user.id,
                row["character_id"],
                row["kind"],
                digest,
                row["embedding"],
                now,
            )
            await conn.execute("delete from public.memories where id = $1 and user_id = $2", memory_id, user.id)
        await self._audit.log(
            "memory.delete",
            user_id=user.id,
            character_id=row["character_id"],
            payload={
                "memory_id": row["id"],
                "source": "user",
                "kind": row["kind"],
                "status": row["status"],
                "content": row["content"],
                "importance": float(row["importance"]),
                "tags": list(row["tags"] or []),
                "was_user_edited": row["is_user_edited"],
                "tombstone_id": tombstone_id,
                "content_hash": digest,
            },
            at=now,
        )
        for change in cancelled:
            await self._audit.log(
                "promise.status_change",
                user_id=user.id,
                character_id=change.character_id,
                payload=change.audit_payload(source_memory_id=memory_id),
                at=now,
            )

    # ================================================================== 約束
    async def list_promises(
        self, user: CurrentUser, character_id: UUID, *, include_closed: bool = False
    ) -> ListPromisesResponse:
        """既定は未達（pending / mentioned）だけ。期日の近い順（期日なしは最後）。"""
        rows = await self._pool.fetch(
            f"""
            select {PROMISE_COLUMNS} from public.promises
             where user_id = $1 and character_id = $2 and ($3 or status = any($4))
             order by (status = any($4)) desc, due_at asc nulls last, created_at desc
             limit $5
            """,  # noqa: S608 - 列名は定数
            user.id,
            character_id,
            include_closed,
            sorted(OPEN_STATUSES),
            PROMISES_LIST_LIMIT,
        )
        return ListPromisesResponse(promises=[promise_dto(r) for r in rows])

    async def update_promise(self, user: CurrentUser, promise_id: UUID, request: UpdatePromiseRequest) -> PromiseDTO:
        """ユーザーによる完了・取り消し。同じ状態への変更は何もせずに現在の状態を返す（再送に強くする）。"""
        now = self._clock.now()
        async with self._pool.acquire() as conn, conn.transaction():
            change = await change_status(
                conn, promise_id=promise_id, after=request.status, now=now, source="user", user_id=user.id
            )
            if change is None:
                row = await conn.fetchrow(
                    f"select {PROMISE_COLUMNS} from public.promises where id = $1 and user_id = $2",  # noqa: S608
                    promise_id,
                    user.id,
                )
                if row is None:
                    raise not_found("約束が見つかりません。")
                return promise_dto(row)
        await self._audit.log(
            "promise.status_change",
            user_id=user.id,
            character_id=change.character_id,
            payload=change.audit_payload(),
            at=now,
        )
        retired = change.retired_memory_payload()
        if retired is not None:
            await self._audit.log(
                "memory.supersede", user_id=user.id, character_id=change.character_id, payload=retired, at=now
            )
        return promise_dto(change.row)

    # ================================================================== 内部
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
            at=self._clock.now(),
        )
        raise ApiError(422, "moderation_blocked", _MEMORY_BLOCKED_MESSAGE)

    async def _embed(
        self,
        text: str,
        *,
        user: CurrentUser,
        character_id: UUID,
        action: Literal["create", "update"],
        memory_id: UUID | None = None,
    ) -> list[float]:
        """記憶の本文を埋め込む。失敗・時間切れは audit `llm.error`（purpose=user_memory）を残して 503。"""
        try:
            async with asyncio.timeout(self._embed_deadline):
                [vector] = await self._embedder.embed([text])
        except (EmbeddingError, TimeoutError) as exc:
            failure = embedding_failure_payload(exc, timeout_seconds=self._embed_deadline)
            logger.error("memory embedding failed", extra={"fields": failure})
            await self._audit.log(
                "llm.error",
                user_id=user.id,
                character_id=character_id,
                payload={
                    "purpose": "user_memory",
                    "action": action,
                    "memory_id": memory_id,
                    **failure,
                    "embedding_model": self._embedder.model_name,
                },
                at=self._clock.now(),
            )
            raise ApiError(503, "llm_unavailable", _EMBEDDING_FAILED_MESSAGE) from exc
        return vector


def _capacity_error(capacity: int) -> ApiError:
    return ApiError(
        422,
        "validation_error",
        f"覚えておける記憶は1人のキャラクターにつき{capacity}件までです。不要な記憶を削除してから追加してください。",
    )
