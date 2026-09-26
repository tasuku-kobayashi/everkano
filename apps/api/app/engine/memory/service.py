"""Memory Engine（仕様 §4 / ENGINE_BRIEF §2.6）: `MemoryService`（app/engine/types.py）の実装。

- 検索（M5, 返答の前）: `retrieve_context` = 厳密検索の候補を総合点（類似度・重要度・新しさ・種類）で並べ、
  呼び方・重要な事実・最新の要約を常に入れる。期日が ±2 日の約束（M6）と、キャラ側の記憶（M8 / C9）も返す。
- 分析（M1〜M4・M6・M8, 返答の後のジョブ `post_turn`）: `process_turns` = LLM 1 回（memory_analysis）で
  add / update / supersede / noop・約束・キャラの発言を得て、埋め込みによる重複判定・墓標（E5）・
  ユーザー編集の保護（E5）・件数の上限（ADR-0024）を通して適用する。状態の変化はすべて監査ログに残す（E9）。
- 要約（M7, ジョブ `memory.summarize`）: `maybe_summarize`（summary.py）。
- 時刻は必ず呼び出し側の `now`（Clock）を使い、DB に書く日時にも明示的に渡す（評価ハーネスの時間の早送り）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Final, Literal
from uuid import UUID

import asyncpg

from app.core.db import Connection, Pool, vector_literal
from app.core.logging import get_logger
from app.engine.memory.analysis import (
    AnalysisInput,
    AnalysisOutput,
    AnalysisOutputError,
    AnalysisPrompt,
    MemoryOpOut,
    MemoryRef,
    PromiseRef,
    mock_context,
    parse_analysis,
)
from app.engine.memory.capacity import EvictedMemory, lock_pair, log_evictions, make_room
from app.engine.memory.config import MemoryConfig
from app.engine.memory.dates import DAY_DUE_TIME, due_at, normalize, today_jst
from app.engine.memory.embedding import EmbeddingClient, EmbeddingError, embedding_failure_payload
from app.engine.memory.guard import DroppedItem, InjectionScreen, filter_output, screen_turns
from app.engine.memory.mock import register_mock_handlers
from app.engine.memory.promises import change_status
from app.engine.memory.ranking import Candidate, lexical_overlap, rank, recency
from app.engine.memory.store import (
    BASE_MEMORIES_SQL,
    CANDIDATES_SQL,
    DUE_PROMISES_SQL,
    FALLBACK_CANDIDATES_SQL,
    INSERT_CHARACTER_MEMORY_SQL,
    INSERT_MEMORY_SQL,
    INSERT_PROMISE_SQL,
    NEAREST_STATEMENT_SQL,
    OPEN_PROMISES_SQL,
    PINNED_SQL,
    RELATED_SQL,
    candidate_from_row,
    fetch_character_rows,
    nearest_active,
    nearest_tombstone,
    to_float,
)
from app.engine.memory.summary import MemorySummarizer, load_summary_template
from app.engine.memory.text import (
    bigram_jaccard,
    content_hash,
    format_date_ja,
    is_trivial_turn,
    mentions_promise,
    normalize_for_hash,
    split_sentences,
)
from app.engine.types import (
    JST,
    CharacterMemoryItem,
    MemoryContext,
    MemoryItem,
    MemoryProcessResult,
    PromiseItem,
    TurnRecord,
)
from app.services.audit import AuditEventType, AuditLogger
from app.services.characters import CHARACTER_COLUMNS, character_from_row, to_numeric
from app.services.llm import LLMClient, LLMError, LLMRequest
from app.services.moderation import Moderator
from app.services.persona import Persona, PersonaRepository
from app.services.prompt import ChatMessage
from app.services.types import MEMORY_TAG_SECRET

logger = get_logger("memory.engine")

ANALYSIS_PURPOSE: Final[str] = "memory_analysis"
MAX_ANALYSIS_ATTEMPTS: Final[int] = 2  # 1 回目 + 検証エラーを添えた再生成 1 回
MAX_SENTENCES_FOR_CONTEXT: Final[int] = 20
_RETRY_INSTRUCTION: Final[str] = (
    "前回の出力は指定のスキーマに合いませんでした（{error}）。説明を付けず、指定の形式の JSON オブジェクトだけを"
    "出力し直してください。"
)


class MemoryEngineUnavailableError(RuntimeError):
    """LLM・埋め込み API の一時的な障害で分析できなかった（再実行で回復し得る。何も書き込んでいない）。"""


@dataclass(frozen=True, slots=True)
class _Event:
    type: AuditEventType
    payload: dict[str, Any]


@dataclass(slots=True)
class _AnalysisOutcome:
    output: AnalysisOutput | None
    error: str | None = None
    model: str | None = None
    latency_ms: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    attempts: int = 0
    messages: list[ChatMessage] = field(default_factory=list)
    raw_outputs: list[str] = field(default_factory=list)


AddOutcome = Literal["created", "merged", "duplicate", "user_edited", "tombstoned", "no_room"]


@dataclass(slots=True)
class _Applier:
    """分析結果を 1 トランザクションで適用する（監査ログはコミット後にまとめて書く）。"""

    config: MemoryConfig
    user_id: UUID
    character_id: UUID
    conversation_id: UUID
    now: datetime
    turns: Sequence[TurnRecord]
    memory_refs: dict[str, MemoryRef]
    promise_refs: dict[str, PromiseRef]
    vectors: dict[str, str]  # 本文 → pgvector のリテラル
    created: list[UUID] = field(default_factory=list)
    updated: list[UUID] = field(default_factory=list)
    superseded: list[UUID] = field(default_factory=list)
    skipped_user_edited: int = 0
    skipped_tombstoned: int = 0
    dropped_low_importance: int = 0
    dropped_capacity: int = 0
    noops: int = 0
    promises_created: list[UUID] = field(default_factory=list)
    promise_status_changes: int = 0
    statements_created: list[UUID] = field(default_factory=list)
    evicted: list[EvictedMemory] = field(default_factory=list)
    events: list[_Event] = field(default_factory=list)
    handled_targets: set[str] = field(default_factory=set)

    # ------------------------------------------------------------------ 共通
    def _turn(self, index: int | None) -> TurnRecord:
        if index is not None and 1 <= index <= len(self.turns):
            return self.turns[index - 1]
        return self.turns[-1]

    def _event(self, event_type: AuditEventType, **payload: Any) -> None:
        self.events.append(_Event(event_type, {"conversation_id": self.conversation_id, **payload}))

    def _skip_user_edited(self, memory_id: UUID, *, op: str, content: str) -> None:
        # E5: ユーザーが書いた・直した記憶は自動で上書き・置き換えしない
        self.skipped_user_edited += 1
        self._event(
            "memory.user_edited_skipped",
            memory_id=memory_id,
            op=op,
            candidate_content_hash=content_hash(content),
        )

    async def _add(
        self,
        conn: Connection,
        *,
        kind: str,
        content: str,
        importance: float,
        secret: bool,
        source_message_id: UUID,
        via: str,
    ) -> tuple[UUID | None, AddOutcome]:
        """墓標 → 既存の記憶との重複 → 件数の上限の順に確かめてから追加する。"""
        literal = self.vectors[content]
        digest = content_hash(content)
        tombstone = await nearest_tombstone(conn, self.user_id, self.character_id, literal, digest)
        if tombstone is not None and (
            tombstone.same_hash or (tombstone.similarity or 0.0) >= self.config.tombstone_similarity
        ):
            # E5: ユーザーが削除した記憶は自動抽出で復活させない（本文は記録しない）
            self.skipped_tombstoned += 1
            self._event(
                "memory.tombstone_suppressed",
                tombstone_id=tombstone.id,
                kind=kind,
                content_hash=digest,
                same_hash=tombstone.same_hash,
                similarity=tombstone.similarity,
                via=via,
            )
            return None, "tombstoned"
        near = await nearest_active(conn, self.user_id, self.character_id, literal)
        if near is not None and near.similarity >= self.config.dedup_similarity:
            if near.is_user_edited:
                self._skip_user_edited(near.id, op="dedupe", content=content)
                return near.id, "user_edited"
            if normalize_for_hash(near.content) == normalize_for_hash(content) and importance <= near.importance:
                self.noops += 1
                return near.id, "duplicate"
            # M3: 同じ内容の記憶は統合する（新しい言い方に更新し、重要度は大きい方）
            await conn.execute(
                """
                update public.memories
                   set content = $3, importance = greatest(importance, $4::numeric),
                       embedding = $5::text::extensions.vector,
                       tags = case when $6 and not ($7 = any(tags)) then array_append(tags, $7) else tags end,
                       source_message_id = coalesce($8, source_message_id),
                       source_conversation_id = coalesce($9, source_conversation_id),
                       updated_at = $10
                 where id = $1 and user_id = $2 and not is_user_edited
                """,
                near.id,
                self.user_id,
                content,
                to_numeric(importance),
                literal,
                secret,
                MEMORY_TAG_SECRET,
                source_message_id,
                self.conversation_id,
                self.now,
            )
            if near.id not in self.updated and near.id not in self.created:
                self.updated.append(near.id)
            self._event(
                "memory.update",
                memory_id=near.id,
                source="analysis",
                op="dedupe_merge",
                kind=near.kind,
                before=near.content,
                after=content,
                similarity=round(near.similarity, 4),
                importance=importance,
                source_message_id=source_message_id,
            )
            return near.id, "merged"
        has_room, removed = await make_room(
            conn, user_id=self.user_id, character_id=self.character_id, capacity=self.config.max_per_character
        )
        self.evicted.extend(removed)
        if not has_room:
            self.dropped_capacity += 1
            return None, "no_room"
        memory_id: UUID = await conn.fetchval(
            INSERT_MEMORY_SQL,
            self.user_id,
            self.character_id,
            kind,
            content,
            to_numeric(importance),
            [MEMORY_TAG_SECRET] if secret else [],
            literal,
            source_message_id,
            self.conversation_id,
            self.now,
        )
        self.created.append(memory_id)
        self._event(
            "memory.create",
            memory_id=memory_id,
            source="analysis",
            via=via,
            kind=kind,
            content=content,
            importance=importance,
            tags=[MEMORY_TAG_SECRET] if secret else [],
            source_message_id=source_message_id,
        )
        return memory_id, "created"

    # ------------------------------------------------------------------ 記憶の操作
    async def apply_op(self, conn: Connection, op: MemoryOpOut) -> None:
        if op.op == "noop":
            self.noops += 1
            return
        content = op.content or ""
        turn = self._turn(op.turn)
        target = self.memory_refs.get(op.target) if op.target is not None else None
        if op.op in {"update", "supersede"} and (target is None or op.target in self.handled_targets):
            # 参照先が無い（捏造・重複した操作）→ 新しい情報として重複判定つきで追加する
            target = None
        if op.op == "update" and target is not None:
            self.handled_targets.add(target.ref)
            await self._update(conn, target, op, turn)
            return
        if op.op == "supersede" and target is not None:
            self.handled_targets.add(target.ref)
            await self._supersede(conn, target, op, turn)
            return
        importance = op.importance if op.importance is not None else 0.6
        if op.op == "add" and importance < self.config.importance_threshold:
            self.dropped_low_importance += 1
            return
        await self._add(
            conn,
            kind=op.kind or (target.kind if target is not None else "fact"),
            content=content,
            importance=importance,
            secret=op.secret,
            source_message_id=turn.user_message_id,
            via=op.op,
        )

    async def _locked_target(self, conn: Connection, target: MemoryRef) -> dict[str, Any] | None:
        row = await conn.fetchrow(
            """
            select id, kind, content, importance, tags, status, is_user_edited from public.memories
             where id = $1 and user_id = $2 and character_id = $3
             for update
            """,
            target.id,
            self.user_id,
            self.character_id,
        )
        return dict(row) if row is not None else None

    async def _update(self, conn: Connection, target: MemoryRef, op: MemoryOpOut, turn: TurnRecord) -> None:
        content = op.content or ""
        row = await self._locked_target(conn, target)
        if row is None or row["status"] != "active":
            await self._add(
                conn,
                kind=op.kind or target.kind,
                content=content,
                importance=op.importance or target.importance,
                secret=op.secret,
                source_message_id=turn.user_message_id,
                via="update_fallback",
            )
            return
        if row["is_user_edited"]:
            self._skip_user_edited(target.id, op="update", content=content)
            return
        if normalize_for_hash(row["content"]) == normalize_for_hash(content):
            self.noops += 1
            return
        kind = row["kind"] if row["kind"] == "summary" or op.kind is None else op.kind
        importance = max(to_float(row["importance"]), op.importance or 0.0)
        tags = list(row["tags"] or [])
        if op.secret and MEMORY_TAG_SECRET not in tags:
            tags.append(MEMORY_TAG_SECRET)
        await conn.execute(
            """
            update public.memories
               set content = $2, kind = $3, importance = $4, tags = $5, embedding = $6::text::extensions.vector,
                   source_message_id = $7, source_conversation_id = $8, updated_at = $9
             where id = $1
            """,
            target.id,
            content,
            kind,
            to_numeric(importance),
            tags,
            self.vectors[content],
            turn.user_message_id,
            self.conversation_id,
            self.now,
        )
        if target.id not in self.updated:
            self.updated.append(target.id)
        self._event(
            "memory.update",
            memory_id=target.id,
            source="analysis",
            op="update",
            kind=kind,
            before=row["content"],
            after=content,
            importance=importance,
            source_message_id=turn.user_message_id,
        )

    async def _supersede(self, conn: Connection, target: MemoryRef, op: MemoryOpOut, turn: TurnRecord) -> None:
        content = op.content or ""
        importance = op.importance if op.importance is not None else max(target.importance, 0.7)
        row = await self._locked_target(conn, target)
        if row is not None and row["is_user_edited"]:
            # E5: ユーザーの記憶は置き換えない。新しい情報だけを（重複判定つきで）追加する
            self._skip_user_edited(target.id, op="supersede", content=content)
        if row is None or row["status"] != "active" or row["is_user_edited"]:
            await self._add(
                conn,
                kind=op.kind or target.kind,
                content=content,
                importance=importance,
                secret=op.secret,
                source_message_id=turn.user_message_id,
                via="supersede_fallback",
            )
            return
        literal = self.vectors[content]
        digest = content_hash(content)
        tombstone = await nearest_tombstone(conn, self.user_id, self.character_id, literal, digest)
        if tombstone is not None and (
            tombstone.same_hash or (tombstone.similarity or 0.0) >= self.config.tombstone_similarity
        ):
            self.skipped_tombstoned += 1
            self._event(
                "memory.tombstone_suppressed",
                tombstone_id=tombstone.id,
                kind=op.kind or target.kind,
                content_hash=digest,
                same_hash=tombstone.same_hash,
                similarity=tombstone.similarity,
                via="supersede",
            )
            return
        has_room, removed = await make_room(
            conn, user_id=self.user_id, character_id=self.character_id, capacity=self.config.max_per_character
        )
        self.evicted.extend(removed)
        if not has_room:
            self.dropped_capacity += 1
            return
        kind = op.kind or row["kind"]
        new_id: UUID = await conn.fetchval(
            INSERT_MEMORY_SQL,
            self.user_id,
            self.character_id,
            kind,
            content,
            to_numeric(importance),
            [MEMORY_TAG_SECRET] if op.secret else [],
            literal,
            turn.user_message_id,
            self.conversation_id,
            self.now,
        )
        # M4: 古い記憶は消さずに履歴として残す（status = superseded, superseded_by = 新しい記憶）
        await conn.execute(
            """
            update public.memories
               set status = 'superseded', superseded_by = $2, superseded_at = $3, updated_at = $3
             where id = $1
            """,
            target.id,
            new_id,
            self.now,
        )
        self.created.append(new_id)
        self.superseded.append(target.id)
        self._event(
            "memory.create",
            memory_id=new_id,
            source="analysis",
            via="supersede",
            kind=kind,
            content=content,
            importance=importance,
            tags=[MEMORY_TAG_SECRET] if op.secret else [],
            source_message_id=turn.user_message_id,
        )
        self._event(
            "memory.supersede",
            old_memory_id=target.id,
            new_memory_id=new_id,
            kind=kind,
            old_content=row["content"],
            new_content=content,
            source_message_id=turn.user_message_id,
        )

    # ------------------------------------------------------------------ 約束
    async def apply_promise(
        self,
        conn: Connection,
        *,
        content: str,
        memory_text: str,
        due: datetime | None,
        precision: str,
        importance: float,
        turn: TurnRecord,
        created_promises: list[tuple[str, datetime | None]],
    ) -> None:
        due_day = due.astimezone(JST).date() if due is not None else None
        for other_content, other_due in [
            *((p.content, p.due_at) for p in self.promise_refs.values()),
            *created_promises,
        ]:
            other_day = other_due.astimezone(JST).date() if other_due is not None else None
            if other_day == due_day and (
                normalize_for_hash(other_content) == normalize_for_hash(content)
                or bigram_jaccard(other_content, content) >= self.config.promise_dedup_similarity
            ):
                return  # 既存（または同じ分析内）の約束と同じ
        memory_id, outcome = await self._add(
            conn,
            kind="promise",
            content=memory_text,
            importance=max(importance, 0.8),
            secret=False,
            source_message_id=turn.user_message_id,
            via="promise",
        )
        if outcome == "tombstoned":
            return  # 削除された約束の記憶は、約束ごと作り直さない（E5）
        promise_id: UUID = await conn.fetchval(
            INSERT_PROMISE_SQL,
            self.user_id,
            self.character_id,
            content,
            due,
            precision,
            memory_id,
            turn.user_message_id,
            self.now,
        )
        created_promises.append((content, due))
        self.promises_created.append(promise_id)
        self._event(
            "promise.create",
            promise_id=promise_id,
            content=content,
            due_at=due,
            due_precision=precision,
            source_memory_id=memory_id,
            source_message_id=turn.user_message_id,
        )

    async def apply_promise_update(self, conn: Connection, ref: PromiseRef, status: str) -> None:
        if status not in {"mentioned", "done", "cancelled"}:
            return
        change = await change_status(
            conn,
            promise_id=ref.id,
            after=status,  # type: ignore[arg-type]
            now=self.now,
            source="analysis",
            user_id=self.user_id,
        )
        if change is None:
            return
        self.promise_status_changes += 1
        self.events.append(
            _Event("promise.status_change", {"conversation_id": self.conversation_id, **change.audit_payload()})
        )
        retired = change.retired_memory_payload(conversation_id=self.conversation_id)
        if retired is not None and change.memory_retired is not None:
            self.superseded.append(change.memory_retired)
            self.events.append(_Event("memory.supersede", retired))

    # ------------------------------------------------------------------ キャラ側の記憶（M8）
    async def apply_statement(self, conn: Connection, *, content: str, occurred_at: datetime, turn: TurnRecord) -> None:
        literal = self.vectors[content]
        near = await conn.fetchrow(NEAREST_STATEMENT_SQL, self.character_id, self.user_id, literal)
        if near is not None and to_float(near["similarity"]) >= self.config.dedup_similarity:
            return
        statement_id: UUID = await conn.fetchval(
            INSERT_CHARACTER_MEMORY_SQL,
            self.character_id,
            self.user_id,
            content,
            occurred_at,
            turn.character_message_id,
            literal,
            self.now,
        )
        self.statements_created.append(statement_id)
        self._event(
            "character_memory.create",
            character_memory_id=statement_id,
            kind="self_statement",
            content=content,
            occurred_at=occurred_at,
            source_message_id=turn.character_message_id,
        )


class MemoryEngineService:
    """MemoryService（app/engine/types.py）の実装。コンテナ（core）が 1 つ生成して共有する。"""

    def __init__(
        self,
        *,
        pool: Pool,
        llm: LLMClient,
        embedder: EmbeddingClient,
        audit: AuditLogger,
        personas: PersonaRepository,
        moderator: Moderator,
        config: MemoryConfig | None = None,
    ) -> None:
        self._pool = pool
        self._llm = llm
        self._embedder = embedder
        self._audit = audit
        self._personas = personas
        self._moderator = moderator
        self._config = config or MemoryConfig()
        # テンプレートは起動時に読み込んで検証する（不正なら起動しない）
        self._analysis_prompt = AnalysisPrompt.load(self._config.prompts_dir)
        self._summarizer = MemorySummarizer(
            pool=pool,
            llm=llm,
            embedder=embedder,
            audit=audit,
            moderator=moderator,
            config=self._config,
            template=load_summary_template(self._config.prompts_dir),
            persona_for=self._persona_for,
        )
        register_mock_handlers()

    @property
    def config(self) -> MemoryConfig:
        return self._config

    @property
    def summarizer(self) -> MemorySummarizer:
        return self._summarizer

    # ================================================================== 検索（返答の前）
    async def embed_query(
        self, text: str, *, user_id: UUID, character_id: UUID, conversation_id: UUID
    ) -> list[float] | None:
        """検索用の埋め込み。失敗・EMBEDDING_TIMEOUT_SECONDS 超過なら None（意味検索を省略して返答は続ける）。

        失敗は audit `llm.error`（purpose=embedding_query）に残す（ADR-0022）。ここでの TimeoutError は内側の
        asyncio.timeout のもので、呼び出し側の締め切りの失効は CancelledError として外へ伝わる。
        """
        timeout = self._config.embedding_timeout_seconds
        try:
            async with asyncio.timeout(timeout):
                [vector] = await self._embedder.embed([text])
        except (EmbeddingError, TimeoutError) as exc:
            failure = embedding_failure_payload(exc, timeout_seconds=timeout)
            logger.error("query embedding failed; skipping semantic retrieval", extra={"fields": failure})
            await self._audit.log(
                "llm.error",
                user_id=user_id,
                character_id=character_id,
                payload={
                    "purpose": "embedding_query",
                    "conversation_id": conversation_id,
                    **failure,
                    "embedding_model": self._embedder.model_name,
                },
            )
            return None
        return vector

    async def retrieve_context(
        self,
        *,
        user_id: UUID,
        character_id: UUID,
        query_text: str,
        query_embedding: list[float] | None,
        now: datetime,
    ) -> MemoryContext:
        config = self._config
        literal = vector_literal(query_embedding) if query_embedding is not None else None
        async with self._pool.acquire() as conn:
            pinned_rows = await conn.fetch(
                PINNED_SQL,
                user_id,
                character_id,
                config.always_relationship,
                config.always_summaries,
                to_numeric(config.core_fact_min_importance),
                config.core_facts,
            )
            if literal is not None:
                candidate_rows = await conn.fetch(CANDIDATES_SQL, user_id, character_id, literal, config.candidate_pool)
            else:
                candidate_rows = await conn.fetch(FALLBACK_CANDIDATES_SQL, user_id, character_id, config.candidate_pool)
            # 期日が「今日 ± 2 日」（日本時間の日付）に入る約束。日付だけの約束（期日 = その日の 12:00）も日単位で数える
            today = today_jst(now)
            window_days = max(config.promise_window.days, 0)
            promise_rows = await conn.fetch(
                DUE_PROMISES_SQL,
                user_id,
                character_id,
                datetime.combine(today - timedelta(days=window_days), time.min, tzinfo=JST),
                datetime.combine(today + timedelta(days=window_days + 1), time.min, tzinfo=JST)
                - timedelta(microseconds=1),
                config.max_context_promises,
            )
            character_rows = await fetch_character_rows(
                conn,
                character_id=character_id,
                user_id=user_id,
                shared_since=now - timedelta(days=config.shared_event_days),
                now=now,
                literal=literal,
            )
        promises = tuple(
            PromiseItem(
                id=r["id"],
                content=r["content"],
                due_at=r["due_at"],
                due_precision=r["due_precision"],
                status=r["status"],
            )
            for r in promise_rows
        )
        pinned_facts = [candidate_from_row(r) for r in pinned_rows if r["slot"] in (1, 3)]
        summaries = [candidate_from_row(r) for r in pinned_rows if r["slot"] == 2]
        reserved = summaries[:1]
        reserved_chars = sum(len(s.content) for s in reserved)
        ranked = rank(
            (
                replace(c, lexical=lexical_overlap(query_text, c.content))
                for c in map(candidate_from_row, candidate_rows)
            ),
            pinned=pinned_facts,
            now=now,
            config=config,
            use_similarity=literal is not None,
            exclude_ids=frozenset(s.id for s in summaries),
            max_items=config.max_memories - len(reserved),
            max_chars=config.memory_chars_budget - reserved_chars,
        )
        items = [_memory_item(r.candidate, r.score) for r in ranked]
        used_chars = sum(len(i.content) for i in items)
        for summary in summaries:
            # 最新の要約は常に入れる（2 件目は上限に余裕があれば）
            if len(items) >= config.max_memories:
                break
            if summary is not summaries[0] and used_chars + len(summary.content) > config.memory_chars_budget:
                break
            items.append(_memory_item(summary, None))
            used_chars += len(summary.content)
        return MemoryContext(
            memories=tuple(items),
            character_memories=self._rank_character_memories(character_rows, now),
            promises=promises,
            retrieval_skipped=query_embedding is None,
        )

    def _rank_character_memories(self, rows: Sequence[Any], now: datetime) -> tuple[CharacterMemoryItem, ...]:
        config = self._config
        scored: list[tuple[float, Any]] = []
        for row in rows:
            anchor: datetime = row["occurred_at"] or row["created_at"]
            rec = recency(
                created_at=anchor,
                last_referenced_at=None,
                now=now,
                half_life_days=config.character_memory_half_life_days,
            )
            similarity = min(max(to_float(row["similarity"]), 0.0), 1.0)
            scored.append((0.6 * similarity + 0.4 * rec, row))
        scored.sort(key=lambda pair: (-pair[0], -(pair[1]["occurred_at"] or pair[1]["created_at"]).timestamp()))
        items: list[CharacterMemoryItem] = []
        used = 0
        for _, row in scored:
            if len(items) >= config.character_memory_limit:
                break
            if used + len(row["content"]) > config.character_memory_chars_budget:
                continue
            used += len(row["content"])
            items.append(
                CharacterMemoryItem(
                    id=row["id"],
                    kind=row["kind"],
                    content=row["content"],
                    occurred_at=row["occurred_at"],
                    is_shared=row["user_id"] is None,
                )
            )
        # プロンプトでは時系列の方が読みやすい
        items.sort(key=lambda i: i.occurred_at or datetime.min.replace(tzinfo=UTC))
        return tuple(items)

    async def mark_referenced(self, *, memory_ids: Sequence[UUID], now: datetime) -> None:
        """プロンプトに入れた記憶の最終参照日時・参照回数を更新する（返答の後にバックグラウンドで呼ばれる）。

        updated_at は変えない（参照の記録は記憶の内容の変更ではない。マイグレーション 20260926100000_memory.sql）。
        """
        ids = list(dict.fromkeys(memory_ids))
        if not ids:
            return
        await self._pool.execute(
            """
            update public.memories
               set last_referenced_at = greatest(coalesce(last_referenced_at, $2), $2),
                   reference_count = reference_count + 1
             where id = any($1::uuid[])
            """,
            ids,
            now,
        )

    # ================================================================== 約束（M6）
    async def due_promises(
        self, *, user_id: UUID, character_id: UUID, now: datetime, window: timedelta
    ) -> Sequence[PromiseItem]:
        """未達（pending / mentioned）で、期日が now ± window に入る約束（期日の早い順）。"""
        rows = await self._pool.fetch(DUE_PROMISES_SQL, user_id, character_id, now - window, now + window, 50)
        return [
            PromiseItem(
                id=r["id"],
                content=r["content"],
                due_at=r["due_at"],
                due_precision=r["due_precision"],
                status=r["status"],
            )
            for r in rows
        ]

    async def mark_promise_mentioned(self, *, promise_id: UUID, now: datetime) -> None:
        """キャラが約束を話題にした（自発メッセージなど）。pending のときだけ mentioned にする。"""
        async with self._pool.acquire() as conn, conn.transaction():
            change = await change_status(conn, promise_id=promise_id, after="mentioned", now=now, source="proactive")
        if change is not None:
            await self._audit.log(
                "promise.status_change",
                user_id=change.user_id,
                character_id=change.character_id,
                payload=change.audit_payload(),
                at=now,
            )

    # ================================================================== 要約（M7）
    async def maybe_summarize(self, *, conversation_id: UUID, user_id: UUID, character_id: UUID, now: datetime) -> None:
        await self._summarizer.maybe_summarize(
            conversation_id=conversation_id, user_id=user_id, character_id=character_id, now=now
        )

    # ================================================================== 分析（返答の後）
    async def process_turns(
        self,
        *,
        user_id: UUID,
        character_id: UUID,
        conversation_id: UUID,
        turns: Sequence[TurnRecord],
        now: datetime,
    ) -> MemoryProcessResult:
        """新しいターンをまとめて分析し、記憶・約束・キャラ側の記憶に反映する。

        - Gate #1 で差し止めたターン・E6 の安全対応をしたターンは分析しない（本文を記憶に残さない）。
        - 一時的な障害（LLM の 429 / 5xx / タイムアウト、埋め込み API の障害）は `MemoryEngineUnavailableError` を投げる
          （ジョブ基盤がバックオフして再実行する。何も書き込んでいない）。
        - 同じ入力では回復しない失敗（スキーマに合わない出力が 2 回続いた、LLM が内容で拒否した）は例外にせず
          `error` に理由（`invalid_output:` / `llm_rejected:`）を入れて返す。どちらも監査ログ `llm.error` に残す。
        """
        config = self._config
        usable = [t for t in turns if not t.moderated and not t.safety_triggered]
        skipped_turns = len(turns) - len(usable)
        usable = usable[-config.analysis_max_turns :]
        if not usable:
            return MemoryProcessResult()
        if config.skip_trivial_batches and all(is_trivial_turn(t.user_text, t.reply_text) for t in usable):
            # 相づち・あいさつだけのターン（「うん」「ただいま」）は LLM で分析しない（E7 のコスト）。
            # キャラが返答で約束に触れた（「おかえり！面接どうだった？」）ことだけは規則で記録する（M6）
            await self._mark_mentioned_by_rule(
                user_id=user_id, character_id=character_id, conversation_id=conversation_id, turns=usable, now=now
            )
            return MemoryProcessResult()
        if not await self._conversation_exists(user_id, character_id, conversation_id):
            # 退会・削除された会話（ジョブの待ち時間の間に消えた）→ 何もしない
            return MemoryProcessResult(error="conversation_not_found")
        persona = await self._persona_for(character_id)
        if persona is None:
            return MemoryProcessResult(error="character_not_found")

        # 0. 記憶経由の注入の防止（guard.py）: 設定・関係・評価を書き換えようとする発言は分析に渡さない
        screen = screen_turns(usable)
        if screen.all_flagged:
            await self._mark_mentioned_by_rule(
                user_id=user_id, character_id=character_id, conversation_id=conversation_id, turns=usable, now=now
            )
            await self._audit_injection(
                screen, [], user_id=user_id, character_id=character_id, conversation_id=conversation_id, now=now
            )
            return MemoryProcessResult()
        analysis_turns = screen.turns

        # 1. 関連する既存の記憶・未達の約束
        sentences = list(
            dict.fromkeys(s for t in analysis_turns for s in split_sentences(t.user_text) if len(s.strip()) >= 4)
        )[:MAX_SENTENCES_FOR_CONTEXT]
        try:
            sentence_vectors = await self._embed(sentences)
        except (EmbeddingError, TimeoutError) as exc:
            return await self._embedding_failed(
                exc,
                purpose="memory_analysis_context",
                user_id=user_id,
                character_id=character_id,
                conversation_id=conversation_id,
                now=now,
            )
        async with self._pool.acquire() as conn:
            related_rows = (
                await conn.fetch(
                    RELATED_SQL,
                    user_id,
                    character_id,
                    [vector_literal(v) for v in sentence_vectors],
                    config.analysis_related_per_sentence,
                )
                if sentence_vectors
                else []
            )
            base_rows = await conn.fetch(BASE_MEMORIES_SQL, user_id, character_id, config.analysis_base_memories)
            promise_rows = await conn.fetch(OPEN_PROMISES_SQL, user_id, character_id, config.analysis_pending_promises)
        memory_refs = self._context_memories(related_rows, base_rows)
        promise_refs = [
            PromiseRef(
                ref=f"p{i}",
                id=r["id"],
                content=r["content"],
                due_at=r["due_at"],
                due_precision=r["due_precision"],
                status=r["status"],
                event_id=r["event_id"],
            )
            for i, r in enumerate(promise_rows, start=1)
        ]
        data = AnalysisInput(
            persona=persona,
            now=now,
            turns=analysis_turns,
            memories=memory_refs,
            promises=promise_refs,
            threshold=config.importance_threshold,
        )

        # 2. LLM（JSON。スキーマ検証 → 1 回だけ再生成）
        outcome = await self._run_analysis(
            data, user_id=user_id, character_id=character_id, conversation_id=conversation_id, now=now
        )
        if outcome.output is None:
            return MemoryProcessResult(error=outcome.error)
        output, dropped = filter_output(outcome.output, screen.flagged_indexes)
        output = output.truncated(
            max_ops=config.max_ops, max_promises=config.max_promises, max_statements=config.max_character_statements
        )
        if screen.flagged or dropped:
            await self._audit_injection(
                screen, dropped, user_id=user_id, character_id=character_id, conversation_id=conversation_id, now=now
            )

        # 3. 新しい本文をまとめて埋め込む
        today = today_jst(now)
        promise_plans = []
        for promise in output.promises:
            if promise.due_date is not None and not (
                today - timedelta(days=1) <= promise.due_date <= today + timedelta(days=400)
            ):
                # 過去・遠すぎる期日（日付の解決の誤り）→ 期日なしの約束にはせず捨てる（過去の出来事は約束ではない）
                continue
            due = due_at(promise.due_date, promise.due_time, promise.due_precision)
            memory_text = promise.memory or _default_promise_memory(promise.content, due, promise.due_precision, today)
            promise_plans.append((promise, due, memory_text))
        statement_plans = [
            (s, _occurred_at(s.occurred_date, self._turn_of(usable, s.turn), now)) for s in output.character_statements
        ]
        texts = [
            *(op.content for op in output.memories if op.op != "noop" and op.content),
            *(memory_text for _, _, memory_text in promise_plans),
            *(s.content for s, _ in statement_plans),
        ]
        try:
            unique_texts = list(dict.fromkeys(texts))
            vectors = await self._embed(unique_texts)
        except (EmbeddingError, TimeoutError) as exc:
            return await self._embedding_failed(
                exc,
                purpose="memory_save",
                user_id=user_id,
                character_id=character_id,
                conversation_id=conversation_id,
                now=now,
            )

        # 4. 1 トランザクションで適用（同じペアの同時処理はアドバイザリーロックで直列化）
        applier = _Applier(
            config=config,
            user_id=user_id,
            character_id=character_id,
            conversation_id=conversation_id,
            now=now,
            turns=usable,
            memory_refs={m.ref: m for m in memory_refs},
            promise_refs={p.ref: p for p in promise_refs},
            vectors={text: vector_literal(v) for text, v in zip(unique_texts, vectors, strict=True)},
        )
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                await lock_pair(conn, user_id, character_id)
                for op in output.memories:
                    await applier.apply_op(conn, op)
                created_promises: list[tuple[str, datetime | None]] = []
                for promise, due, memory_text in promise_plans:
                    await applier.apply_promise(
                        conn,
                        content=promise.content,
                        memory_text=memory_text,
                        due=due,
                        precision=promise.due_precision,
                        importance=promise.importance,
                        turn=self._turn_of(usable, promise.turn),
                        created_promises=created_promises,
                    )
                for update in output.promise_updates:
                    ref = applier.promise_refs.get(update.target)
                    if ref is not None:
                        await applier.apply_promise_update(conn, ref, update.status)
                for statement, occurred in statement_plans:
                    await applier.apply_statement(
                        conn,
                        content=statement.content,
                        occurred_at=occurred,
                        turn=self._turn_of(usable, statement.turn),
                    )
        except asyncpg.ForeignKeyViolationError:
            # 分析の途中でユーザー・キャラ・会話が削除された（退会・テストの後片付け）→ 何も書かずに終える
            # （ジョブを失敗させて再試行・dead を繰り返さない）。まだ存在するなら本当の不整合なので投げ直す
            if await self._conversation_exists(user_id, character_id, conversation_id):
                raise
            logger.info(
                "conversation vanished during memory analysis; nothing saved",
                extra={"fields": {"conversation_id": str(conversation_id), "user_id": str(user_id)}},
            )
            return MemoryProcessResult(error="conversation_not_found")

        # 5. 監査ログ（コミット後）
        for event in applier.events:
            await self._audit.log(event.type, user_id=user_id, character_id=character_id, payload=event.payload, at=now)
        await log_evictions(
            self._audit,
            applier.evicted,
            user_id=user_id,
            character_id=character_id,
            capacity=config.max_per_character,
            now=now,
        )
        result = MemoryProcessResult(
            created=tuple(applier.created),
            updated=tuple(applier.updated),
            superseded=tuple(applier.superseded),
            skipped_user_edited=applier.skipped_user_edited,
            skipped_tombstoned=applier.skipped_tombstoned,
            promises_created=tuple(applier.promises_created),
            character_memories_created=tuple(applier.statements_created),
        )
        await self._audit_analysis(
            outcome,
            output,
            applier,
            user_id=user_id,
            character_id=character_id,
            conversation_id=conversation_id,
            turns=usable,
            skipped_turns=skipped_turns,
            context_memories=len(memory_refs),
            context_promises=len(promise_refs),
            now=now,
        )
        return result

    # ------------------------------------------------------------------ 内部
    async def _audit_injection(
        self,
        screen: InjectionScreen,
        dropped: Sequence[DroppedItem],
        *,
        user_id: UUID,
        character_id: UUID,
        conversation_id: UUID,
        now: datetime,
    ) -> None:
        """記憶にしなかった操作・注入の発言と、適用しなかった出力（本文は残さない）。"""
        await self._audit.log(
            "memory.injection_skipped",
            user_id=user_id,
            character_id=character_id,
            payload={
                "conversation_id": conversation_id,
                "user_message_ids": sorted(str(mid) for mid in screen.flagged),
                "labels": screen.labels,
                "turns_skipped": len(screen.flagged),
                "llm_skipped": screen.all_flagged,
                "dropped": [item.to_dict() for item in dropped],
            },
            at=now,
        )

    async def _mark_mentioned_by_rule(
        self,
        *,
        user_id: UUID,
        character_id: UUID,
        conversation_id: UUID,
        turns: Sequence[TurnRecord],
        now: datetime,
    ) -> None:
        replies = "\n".join(normalize(t.reply_text) for t in turns)
        changes = []
        async with self._pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(OPEN_PROMISES_SQL, user_id, character_id, self._config.analysis_pending_promises)
            for row in rows:
                if row["status"] != "pending" or not mentions_promise(replies, row["content"]):
                    continue
                change = await change_status(
                    conn, promise_id=row["id"], after="mentioned", now=now, source="analysis", user_id=user_id
                )
                if change is not None:
                    changes.append(change)
        for change in changes:
            await self._audit.log(
                "promise.status_change",
                user_id=user_id,
                character_id=character_id,
                payload={"conversation_id": conversation_id, **change.audit_payload(rule="reply_mentions")},
                at=now,
            )

    async def _conversation_exists(self, user_id: UUID, character_id: UUID, conversation_id: UUID) -> bool:
        found: bool = await self._pool.fetchval(
            "select exists (select 1 from public.conversations where id = $1 and user_id = $2 and character_id = $3)",
            conversation_id,
            user_id,
            character_id,
        )
        return found

    async def _persona_for(self, character_id: UUID) -> Persona | None:
        row = await self._pool.fetchrow(
            f"select {CHARACTER_COLUMNS} from public.characters where id = $1",  # noqa: S608 - 列名は定数
            character_id,
        )
        if row is None:
            return None
        return self._personas.for_character(character_from_row(row))

    async def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        # 返答の後のジョブなので検索用より長めに待つ（それでも固まったままにはしない）
        async with asyncio.timeout(max(self._config.embedding_timeout_seconds * 4, 10.0)):
            return await self._embedder.embed(list(texts))

    async def _embedding_failed(
        self,
        exc: BaseException,
        *,
        purpose: str,
        user_id: UUID,
        character_id: UUID,
        conversation_id: UUID,
        now: datetime,
    ) -> MemoryProcessResult:
        failure = embedding_failure_payload(exc)
        logger.error("embedding failed during memory analysis", extra={"fields": {"purpose": purpose, **failure}})
        await self._audit.log(
            "llm.error",
            user_id=user_id,
            character_id=character_id,
            payload={
                "purpose": purpose,
                "conversation_id": conversation_id,
                **failure,
                "embedding_model": self._embedder.model_name,
            },
            at=now,
        )
        status_code = failure.get("status_code")
        if status_code is None or status_code == 429 or status_code >= 500:
            raise MemoryEngineUnavailableError(f"embedding unavailable ({purpose}): {failure['error']}") from exc
        return MemoryProcessResult(error=f"embedding_unavailable: {failure['error']}")

    @staticmethod
    def _turn_of(turns: Sequence[TurnRecord], index: int | None) -> TurnRecord:
        if index is not None and 1 <= index <= len(turns):
            return turns[index - 1]
        return turns[-1]

    def _context_memories(self, related_rows: Sequence[Any], base_rows: Sequence[Any]) -> list[MemoryRef]:
        """分析に添える既存の記憶（似ている順 → 事実・関係性）。上限内に収め、古い順に m1, m2 … を振る。"""
        config = self._config
        chosen: dict[UUID, Any] = {}
        used = 0
        ordered = sorted(related_rows, key=lambda r: -to_float(r["similarity"])) + list(base_rows)
        for row in ordered:
            if row["id"] in chosen:
                continue
            if len(chosen) >= config.analysis_context_memories:
                break
            if used + len(row["content"]) > config.analysis_context_chars:
                continue
            chosen[row["id"]] = row
            used += len(row["content"])
        rows = sorted(chosen.values(), key=lambda r: r["created_at"])
        return [
            MemoryRef(
                ref=f"m{i}",
                id=r["id"],
                kind=r["kind"],
                content=r["content"],
                importance=to_float(r["importance"]),
                is_user_edited=r["is_user_edited"],
                created_at=r["created_at"],
            )
            for i, r in enumerate(rows, start=1)
        ]

    async def _run_analysis(
        self,
        data: AnalysisInput,
        *,
        user_id: UUID,
        character_id: UUID,
        conversation_id: UUID,
        now: datetime,
    ) -> _AnalysisOutcome:
        messages = self._analysis_prompt.render(data)
        outcome = _AnalysisOutcome(output=None, messages=list(messages))
        context = mock_context(data)
        last_error = ""
        while outcome.attempts < MAX_ANALYSIS_ATTEMPTS:
            outcome.attempts += 1
            try:
                result = await self._llm.complete(
                    LLMRequest(
                        purpose=ANALYSIS_PURPOSE,
                        messages=messages,
                        temperature=0.0,
                        max_tokens=self._config.analysis_max_tokens,
                        json_mode=True,
                        model=self._config.analysis_model,
                        mock_context=context,
                    )
                )
            except LLMError as exc:
                logger.error("memory analysis failed", extra={"fields": {"error": str(exc)}})
                await self._audit.log(
                    "llm.error",
                    user_id=user_id,
                    character_id=character_id,
                    payload={
                        "purpose": ANALYSIS_PURPOSE,
                        "conversation_id": conversation_id,
                        "error": str(exc),
                        "status_code": exc.status_code,
                        "attempts": exc.attempts,
                        "retryable": exc.retryable,
                        "usage": outcome.usage or None,
                    },
                    at=now,
                )
                if exc.retryable or exc.status_code is None:
                    raise MemoryEngineUnavailableError(f"memory analysis LLM unavailable: {exc}") from exc
                outcome.error = f"llm_rejected: {exc}"
                return outcome
            outcome.model = result.model
            outcome.latency_ms += result.latency_ms
            for key, value in (result.usage or {}).items():
                outcome.usage[key] = outcome.usage.get(key, 0) + value
            outcome.raw_outputs.append(result.text)
            try:
                outcome.output = parse_analysis(result.text)
                return outcome
            except AnalysisOutputError as exc:
                last_error = str(exc)
                logger.warning(
                    "memory analysis output is invalid",
                    extra={"fields": {"error": last_error, "attempt": outcome.attempts}},
                )
                messages = [
                    *messages,
                    {"role": "assistant", "content": result.text[:4000]},
                    {"role": "user", "content": _RETRY_INSTRUCTION.format(error=last_error)},
                ]
        payload: dict[str, Any] = {
            "purpose": ANALYSIS_PURPOSE,
            "conversation_id": conversation_id,
            "error": f"invalid_output: {last_error}",
            "status_code": None,
            "attempts": outcome.attempts,
            "model": outcome.model,
            "usage": outcome.usage or None,
        }
        if self._config.audit_log_prompts:
            payload["raw_outputs"] = outcome.raw_outputs
        await self._audit.log("llm.error", user_id=user_id, character_id=character_id, payload=payload, at=now)
        outcome.error = f"invalid_output: {last_error}"
        return outcome

    async def _audit_analysis(
        self,
        outcome: _AnalysisOutcome,
        output: AnalysisOutput,
        applier: _Applier,
        *,
        user_id: UUID,
        character_id: UUID,
        conversation_id: UUID,
        turns: Sequence[TurnRecord],
        skipped_turns: int,
        context_memories: int,
        context_promises: int,
        now: datetime,
    ) -> None:
        ops: dict[str, int] = {}
        for op in output.memories:
            ops[op.op] = ops.get(op.op, 0) + 1
        payload: dict[str, Any] = {
            "purpose": ANALYSIS_PURPOSE,
            "conversation_id": conversation_id,
            "turns": len(turns),
            "skipped_turns": skipped_turns,
            "user_message_ids": [t.user_message_id for t in turns],
            "character_message_ids": [t.character_message_id for t in turns],
            "model": outcome.model,
            "latency_ms": outcome.latency_ms,
            "usage": outcome.usage or None,
            "attempts": outcome.attempts,
            "context_memories": context_memories,
            "context_promises": context_promises,
            "ops": ops,
            "created": applier.created,
            "updated": applier.updated,
            "superseded": applier.superseded,
            "noops": applier.noops,
            "skipped_user_edited": applier.skipped_user_edited,
            "skipped_tombstoned": applier.skipped_tombstoned,
            "dropped_low_importance": applier.dropped_low_importance,
            "dropped_capacity": applier.dropped_capacity,
            "promises_created": applier.promises_created,
            "promise_status_changes": applier.promise_status_changes,
            "character_memories_created": applier.statements_created,
        }
        if self._config.audit_log_prompts:
            payload["prompt_messages"] = outcome.messages
            payload["raw_output"] = outcome.raw_outputs[-1] if outcome.raw_outputs else None
        await self._audit.log("memory.analysis", user_id=user_id, character_id=character_id, payload=payload, at=now)


def _memory_item(candidate: Candidate, score: float | None) -> MemoryItem:
    return MemoryItem(
        id=candidate.id,
        kind=candidate.kind,
        content=candidate.content,
        importance=candidate.importance,
        tags=candidate.tags,
        created_at=candidate.created_at,
        score=score,
        is_user_edited=candidate.is_user_edited,
    )


def _default_promise_memory(content: str, due: datetime | None, precision: str, today: date) -> str:
    if due is None:
        return f"ユーザーは「{content}」の予定・約束がある（期日は未定）"
    day = due.astimezone(JST).date()
    label = format_date_ja(day, today=today)
    if precision == "week":
        label = f"{label}の週"
    elif precision == "month":
        label = f"{day.month}月"
    return f"ユーザーは{label}に「{content}」の予定・約束がある"


def _occurred_at(occurred: date | None, turn: TurnRecord, now: datetime) -> datetime:
    """キャラの発言の出来事の日時（日付だけなら日本時間の 12:00。未来にはしない）。"""
    if occurred is None:
        return turn.occurred_at
    value = datetime.combine(occurred, DAY_DUE_TIME, tzinfo=JST).astimezone(UTC)
    return min(value, now)
