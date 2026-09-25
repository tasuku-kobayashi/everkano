"""メモリエンジン（仕様 §9 / BRIEF §2.5）。

3層構造:
- 短期: messages の直近 `MEMORY_SHORT_TERM_TURNS` ターン（1ターン = ユーザー + キャラの2件）
- 中期: 未要約メッセージが 2×`MEMORY_SUMMARY_TRIGGER_TURNS` 件を超えたら、短期ウィンドウより古い部分を
  要約して `tags={'summary'}` の記憶として保存（conversations.summary_cursor で進捗管理）
- 長期: 重要度スコアリング → embedding → pgvector。応答時に類似検索して再注入

検索は (user_id, character_id) に絞った**厳密検索**（MATERIALIZED CTE で全件の距離を計算）。
絞り込み付きの近似検索（HNSW）は該当行を取りこぼすため使わない（ADR-0005）。

ユーザーが編集した記憶（is_user_edited = true）は自動処理で上書きしない。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final
from uuid import UUID

import asyncpg

from app.core.config import Settings
from app.core.db import VECTOR_COSINE_DISTANCE, Connection, Pool, vector_literal
from app.core.logging import get_logger
from app.services.audit import AuditLogger
from app.services.characters import to_numeric
from app.services.embedding import EmbeddingClient, EmbeddingError
from app.services.llm import LLMClient, LLMError, LLMRequest, MockHints, parse_json_object
from app.services.persona import Persona
from app.services.prompt import PromptBuilder
from app.services.types import MEMORY_TAG_SUMMARY, HistoryItem, MemoryCandidate, RetrievedMemory

logger = get_logger("memory")

SUMMARY_IMPORTANCE: Final[float] = 0.7
SUMMARY_MAX_CHARS: Final[int] = 900
MEMORIES_ALWAYS_INCLUDED_SUMMARIES: Final[int] = 2
EXTRACTED_CONTENT_MAX_CHARS: Final[int] = 500
MAX_CANDIDATES_PER_MESSAGE: Final[int] = 5
EXTRACTION_MAX_TOKENS: Final[int] = 600
SUMMARY_MAX_TOKENS: Final[int] = 800

# 演算子名は定数のみを埋め込む（利用者入力は含まない）
_RETRIEVE_SQL: Final[str] = f"""
with pair as materialized (
  select id, content, importance, tags, created_at,
         embedding {VECTOR_COSINE_DISTANCE} $3::text::extensions.vector as distance
    from public.memories
   where user_id = $1 and character_id = $2 and embedding is not null
)
select id, content, importance, tags, created_at, 1 - distance as similarity
  from pair
 order by distance asc
 limit $4
"""  # noqa: S608

_LATEST_SUMMARIES_SQL: Final[str] = """
select id, content, importance, tags, created_at
  from public.memories
 where user_id = $1 and character_id = $2 and $3 = any(tags)
 order by created_at desc
 limit $4
"""

_NEAREST_SQL: Final[str] = f"""
with pair as materialized (
  select id, is_user_edited,
         embedding {VECTOR_COSINE_DISTANCE} $3::text::extensions.vector as distance
    from public.memories
   where user_id = $1 and character_id = $2 and embedding is not null and not ($4 = any(tags))
)
select id, is_user_edited, 1 - distance as similarity from pair order by distance asc limit 1
"""  # noqa: S608


def _to_float(value: Any) -> float:
    return float(value) if value is not None else 0.0


def _row_to_memory(row: asyncpg.Record, *, with_similarity: bool) -> RetrievedMemory:
    return RetrievedMemory(
        id=row["id"],
        content=row["content"],
        importance=_to_float(row["importance"]),
        tags=tuple(row["tags"] or ()),
        similarity=_to_float(row["similarity"]) if with_similarity else None,
        created_at=row["created_at"],
    )


def parse_candidates(text: str) -> list[MemoryCandidate]:
    """抽出 LLM の出力（{"memories": [...]} または [...]）を寛容にパースする。"""
    try:
        data = parse_json_object(text)
    except ValueError:
        logger.warning("memory extraction output is not JSON", extra={"fields": {"output": text[:500]}})
        return []
    items = data.get("memories") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    candidates: list[MemoryCandidate] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        content = content.strip()[:EXTRACTED_CONTENT_MAX_CHARS]
        try:
            importance = float(item.get("importance", 0.0))
        except (TypeError, ValueError):
            continue
        importance = round(min(max(importance, 0.0), 1.0), 2)
        if content in seen:
            continue
        seen.add(content)
        category = item.get("category")
        candidates.append(
            MemoryCandidate(
                content=content,
                importance=importance,
                category=category if isinstance(category, str) else None,
            )
        )
        if len(candidates) >= MAX_CANDIDATES_PER_MESSAGE:
            break
    return candidates


def parse_summary(text: str) -> str:
    try:
        data = parse_json_object(text)
    except ValueError:
        data = None
    summary = data.get("summary") if isinstance(data, dict) else None
    if not isinstance(summary, str) or not summary.strip():
        # JSON で返らなかった場合は本文をそのまま要約とみなす
        summary = text
    return summary.strip()[:SUMMARY_MAX_CHARS]


class MemoryEngine:
    def __init__(
        self,
        *,
        settings: Settings,
        pool: Pool,
        embedder: EmbeddingClient,
        llm: LLMClient,
        prompts: PromptBuilder,
        audit: AuditLogger,
    ) -> None:
        self._settings = settings
        self._pool = pool
        self._embedder = embedder
        self._llm = llm
        self._prompts = prompts
        self._audit = audit
        self._summarizing: set[UUID] = set()

    # ------------------------------------------------------------------ 短期
    async def fetch_short_term(self, conn: Connection, conversation_id: UUID) -> list[HistoryItem]:
        rows = await conn.fetch(
            """
            select id, sender_type, body, created_at from (
              select id, sender_type, body, created_at
                from public.messages
               where conversation_id = $1
               order by created_at desc
               limit $2
            ) recent
            order by created_at asc
            """,
            conversation_id,
            self._settings.short_term_message_limit,
        )
        return [
            HistoryItem(id=r["id"], sender_type=r["sender_type"], body=r["body"], created_at=r["created_at"])
            for r in rows
        ]

    # ------------------------------------------------------------------ 長期（検索）
    async def embed_query(self, text: str) -> list[float] | None:
        try:
            [vector] = await self._embedder.embed([text])
        except EmbeddingError as exc:
            logger.error("query embedding failed; skipping long-term retrieval", extra={"fields": {"error": str(exc)}})
            return None
        return vector

    async def retrieve(
        self, conn: Connection, *, user_id: UUID, character_id: UUID, query_embedding: Sequence[float] | None
    ) -> list[RetrievedMemory]:
        """類似度上位 K 件を重要度で再ランクし、最新の要約（最大2件）を加える。"""
        top: list[RetrievedMemory] = []
        if query_embedding is not None:
            rows = await conn.fetch(
                _RETRIEVE_SQL,
                user_id,
                character_id,
                vector_literal(query_embedding),
                self._settings.memory_retrieval_top_k,
            )
            top = [_row_to_memory(r, with_similarity=True) for r in rows]
            top.sort(key=lambda m: (-m.importance, -(m.similarity or 0.0)))
        summary_rows = await conn.fetch(
            _LATEST_SUMMARIES_SQL,
            user_id,
            character_id,
            MEMORY_TAG_SUMMARY,
            MEMORIES_ALWAYS_INCLUDED_SUMMARIES,
        )
        seen = {m.id for m in top}
        for row in summary_rows:
            if row["id"] not in seen:
                top.append(_row_to_memory(row, with_similarity=False))
                seen.add(row["id"])
        return top

    # ------------------------------------------------------------------ 長期（抽出・保存）
    async def extract_candidates(
        self, persona: Persona, *, history: Sequence[HistoryItem], user_message: str, now: datetime
    ) -> list[MemoryCandidate]:
        """1回の LLM 呼び出しで記憶候補をまとめて抽出する。失敗しても例外は投げない（チャットを止めない）。"""
        request = LLMRequest(
            purpose="memory_extraction",
            messages=self._prompts.extraction_messages(persona, history=history, user_message=user_message),
            temperature=0.0,
            max_tokens=EXTRACTION_MAX_TOKENS,
            json_mode=True,
            hints=MockHints(persona=persona, now=now, user_message=user_message, history=tuple(history)),
        )
        try:
            result = await self._llm.complete(request)
        except LLMError as exc:
            logger.error("memory extraction failed", extra={"fields": {"error": str(exc)}})
            return []
        return parse_candidates(result.text)

    async def save_candidates(
        self,
        *,
        user_id: UUID,
        character_id: UUID,
        source_message_id: UUID | None,
        candidates: Sequence[MemoryCandidate],
    ) -> list[UUID]:
        """重要度が閾値以上の候補を保存する。近似重複は更新（ユーザー編集済みはスキップ）。"""
        threshold = self._settings.memory_importance_threshold
        selected = [c for c in candidates if c.importance >= threshold]
        if not selected:
            return []
        vectors = await self._embedder.embed([c.content for c in selected])
        created: list[tuple[UUID, MemoryCandidate]] = []
        updated: list[tuple[UUID, MemoryCandidate]] = []
        skipped: list[UUID] = []
        async with self._pool.acquire() as conn:
            for candidate, vector in zip(selected, vectors, strict=True):
                literal = vector_literal(vector)
                async with conn.transaction():
                    near = await conn.fetchrow(_NEAREST_SQL, user_id, character_id, literal, MEMORY_TAG_SUMMARY)
                    if near is not None and _to_float(near["similarity"]) >= self._settings.memory_dedup_similarity:
                        if near["is_user_edited"]:
                            skipped.append(near["id"])
                            continue
                        await conn.execute(
                            """
                            update public.memories
                               set content = $1,
                                   importance = greatest(importance, $2::numeric),
                                   embedding = $3::text::extensions.vector,
                                   source_message_id = coalesce($4, source_message_id)
                             where id = $5 and user_id = $6 and not is_user_edited
                            """,
                            candidate.content,
                            to_numeric(candidate.importance),
                            literal,
                            source_message_id,
                            near["id"],
                            user_id,
                        )
                        updated.append((near["id"], candidate))
                        continue
                    memory_id: UUID = await conn.fetchval(
                        """
                        insert into public.memories (user_id, character_id, content, importance, tags,
                                                     embedding, source_message_id, is_user_edited)
                        values ($1, $2, $3, $4, '{}', $5::text::extensions.vector, $6, false)
                        returning id
                        """,
                        user_id,
                        character_id,
                        candidate.content,
                        to_numeric(candidate.importance),
                        literal,
                        source_message_id,
                    )
                    created.append((memory_id, candidate))
        for memory_id, candidate in created:
            await self._audit.log(
                "memory.create",
                user_id=user_id,
                character_id=character_id,
                payload={
                    "memory_id": memory_id,
                    "source": "extraction",
                    "content": candidate.content,
                    "importance": candidate.importance,
                    "category": candidate.category,
                    "source_message_id": source_message_id,
                },
            )
        for memory_id, candidate in updated:
            await self._audit.log(
                "memory.update",
                user_id=user_id,
                character_id=character_id,
                payload={
                    "memory_id": memory_id,
                    "source": "extraction_dedupe",
                    "content": candidate.content,
                    "importance": candidate.importance,
                    "source_message_id": source_message_id,
                },
            )
        if skipped:
            logger.info(
                "skipped extracted memories that duplicate user-edited memories",
                extra={"fields": {"memory_ids": [str(m) for m in skipped]}},
            )
        return [memory_id for memory_id, _ in created]

    # ------------------------------------------------------------------ 中期（要約）
    async def maybe_summarize(
        self, *, conversation_id: UUID, user_id: UUID, character_id: UUID, persona: Persona, now: datetime
    ) -> UUID | None:
        """未要約メッセージが閾値を超えていれば、短期ウィンドウより古い部分を1件の要約記憶にする。"""
        if conversation_id in self._summarizing:
            return None
        self._summarizing.add(conversation_id)
        try:
            return await self._summarize(
                conversation_id=conversation_id,
                user_id=user_id,
                character_id=character_id,
                persona=persona,
                now=now,
            )
        except (EmbeddingError, LLMError) as exc:
            logger.error(
                "mid-term summarization failed",
                extra={"fields": {"conversation_id": str(conversation_id), "error": str(exc)}},
            )
            await self._audit.log(
                "llm.error",
                user_id=user_id,
                character_id=character_id,
                payload={"purpose": "memory_summary", "conversation_id": conversation_id, "error": str(exc)},
            )
            return None
        except Exception:
            # BackgroundTask 内で動くため、ここで必ずログに残す（チャット応答には影響させない）
            logger.exception(
                "mid-term summarization crashed", extra={"fields": {"conversation_id": str(conversation_id)}}
            )
            return None
        finally:
            self._summarizing.discard(conversation_id)

    async def _summarize(
        self, *, conversation_id: UUID, user_id: UUID, character_id: UUID, persona: Persona, now: datetime
    ) -> UUID | None:
        async with self._pool.acquire() as conn:
            cursor: datetime | None = await conn.fetchval(
                "select summary_cursor from public.conversations where id = $1 and user_id = $2",
                conversation_id,
                user_id,
            )
            unsummarized: int = await conn.fetchval(
                """
                select count(*) from public.messages
                 where conversation_id = $1 and created_at > coalesce($2::timestamptz, '-infinity'::timestamptz)
                """,
                conversation_id,
                cursor,
            )
            if unsummarized <= self._settings.summary_trigger_message_count:
                return None
            # 短期ウィンドウ（直近 N 件）の最古メッセージの時刻。これより古い未要約分を要約する
            boundary: datetime | None = await conn.fetchval(
                """
                select created_at from public.messages
                 where conversation_id = $1
                 order by created_at desc
                 offset $2 limit 1
                """,
                conversation_id,
                self._settings.short_term_message_limit - 1,
            )
            if boundary is None:
                return None
            rows = await conn.fetch(
                """
                select id, sender_type, body, created_at from public.messages
                 where conversation_id = $1
                   and created_at > coalesce($2::timestamptz, '-infinity'::timestamptz)
                   and created_at < $3
                 order by created_at asc
                """,
                conversation_id,
                cursor,
                boundary,
            )
        if not rows:
            return None
        transcript = [
            HistoryItem(id=r["id"], sender_type=r["sender_type"], body=r["body"], created_at=r["created_at"])
            for r in rows
        ]
        result = await self._llm.complete(
            LLMRequest(
                purpose="memory_summary",
                messages=self._prompts.summary_messages(persona, transcript=transcript),
                temperature=0.0,
                max_tokens=SUMMARY_MAX_TOKENS,
                json_mode=True,
                hints=MockHints(persona=persona, now=now, history=tuple(transcript)),
            )
        )
        summary = parse_summary(result.text)
        if not summary:
            return None
        [vector] = await self._embedder.embed([summary])
        last = transcript[-1]
        async with self._pool.acquire() as conn, conn.transaction():
            current = await conn.fetchrow(
                "select summary_cursor from public.conversations where id = $1 and user_id = $2 for update",
                conversation_id,
                user_id,
            )
            if current is None or current["summary_cursor"] != cursor:
                # 他のワーカーが先に要約した
                return None
            memory_id: UUID = await conn.fetchval(
                """
                insert into public.memories
                  (user_id, character_id, content, importance, tags, embedding, source_message_id, is_user_edited)
                values ($1, $2, $3, $4, $5, $6::text::extensions.vector, $7, false)
                returning id
                """,
                user_id,
                character_id,
                summary,
                to_numeric(SUMMARY_IMPORTANCE),
                [MEMORY_TAG_SUMMARY],
                vector_literal(vector),
                last.id,
            )
            await conn.execute(
                "update public.conversations set summary_cursor = $1 where id = $2 and user_id = $3",
                last.created_at,
                conversation_id,
                user_id,
            )
        await self._audit.log(
            "memory.summary",
            user_id=user_id,
            character_id=character_id,
            payload={
                "memory_id": memory_id,
                "conversation_id": conversation_id,
                "summarized_messages": len(transcript),
                "summary_cursor": last.created_at,
                "content": summary,
                "model": result.model,
                "latency_ms": result.latency_ms,
            },
        )
        logger.info(
            "conversation summarized",
            extra={"fields": {"conversation_id": str(conversation_id), "messages": len(transcript)}},
        )
        return memory_id
