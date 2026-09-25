"""メモリエンジン（仕様 §9 / BRIEF §2.5）。

3層構造:
- 短期: messages の直近 `MEMORY_SHORT_TERM_TURNS` ターン（1ターン = ユーザー + キャラの2件）
- 中期: 未要約メッセージが 2×`MEMORY_SUMMARY_TRIGGER_TURNS` 件を超えたら、短期ウィンドウより古い部分を
  要約して `tags={'summary'}` の記憶として保存（conversations.summary_cursor で進捗管理）
- 長期: 重要度スコアリング → embedding → pgvector。応答時に類似検索して再注入

検索は (user_id, character_id) に絞った**厳密検索**（MATERIALIZED CTE で全件の距離を計算）。
絞り込み付きの近似検索（HNSW）は該当行を取りこぼすため使わない（ADR-0005）。

ユーザーが編集した記憶（is_user_edited = true）は自動処理で上書きしない。

Gate #1（入力）で差し止めたユーザー発言も messages には保存されるが、LLM に渡す履歴・記憶抽出の文脈・
中期要約には本文を渡さない（`sanitize_history`）。

中期要約は古い順に「会話ログの文字数上限（TRANSCRIPT_MAX_CHARS）に収まる分」ずつ要約し、カーソルは実際に要約に
含めたメッセージまでしか進めない（収まらなかった分は次のチャンクで要約する）。失敗は会話ごとに指数バックオフし、
同じチャンクで失敗が続く・内容で拒否される（4xx）場合はそのチャンクを飛ばしてカーソルを進める（永久に再試行しない）。

ペアあたりの記憶の上限は `memory_capacity.py`（MEMORY_MAX_PER_CHARACTER）。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Final
from uuid import UUID

import asyncpg

from app.core.config import Settings
from app.core.db import VECTOR_COSINE_DISTANCE, Connection, Pool, vector_literal
from app.core.logging import get_logger
from app.services.audit import AuditLogger
from app.services.characters import to_numeric
from app.services.embedding import EmbeddingClient, EmbeddingError, embedding_failure_payload
from app.services.llm import LLMClient, LLMError, LLMRequest, MockHints, parse_json_object
from app.services.memory_capacity import EvictedMemory, lock_pair, make_room
from app.services.moderation import Moderator
from app.services.persona import Persona
from app.services.prompt import ChatMessage, PromptBuilder, fit_transcript_prefix
from app.services.types import MEMORY_TAG_SUMMARY, HistoryItem, MemoryCandidate, RetrievedMemory

logger = get_logger("memory")

SUMMARY_IMPORTANCE: Final[float] = 0.7
SUMMARY_MAX_CHARS: Final[int] = 900
MEMORIES_ALWAYS_INCLUDED_SUMMARIES: Final[int] = 2
EXTRACTED_CONTENT_MAX_CHARS: Final[int] = 500
MAX_CANDIDATES_PER_MESSAGE: Final[int] = 5
EXTRACTION_MAX_TOKENS: Final[int] = 600
SUMMARY_MAX_TOKENS: Final[int] = 800
# Gate #1（入力）で差し止めた発言を LLM に渡すときの置き換え文
MODERATED_PLACEHOLDER: Final[str] = "（不適切な発言のため省略）"

# 中期要約: 1回に DB から読む未要約メッセージの上限（この中から文字数上限に収まる分だけを1チャンクとして要約する）
SUMMARY_FETCH_LIMIT: Final[int] = 200
# 1回のバックグラウンド処理で要約するチャンク数の上限（溜まった分は以後のチャットで少しずつ消化する）
MAX_SUMMARY_CHUNKS_PER_RUN: Final[int] = 3
# 同じチャンクでこの回数失敗したら、そのチャンクは飛ばしてカーソルを進める
SUMMARY_MAX_ATTEMPTS_PER_CHUNK: Final[int] = 3
# 失敗後の再試行までの待ち（指数バックオフ）
SUMMARY_RETRY_BASE_SECONDS: Final[float] = 60.0
SUMMARY_RETRY_MAX_SECONDS: Final[float] = 3600.0
# 内容が原因で拒否されたとみなす LLM の HTTP ステータス（同じ内容で再試行しても通らない）
_CONTENT_REJECTION_STATUS: Final[frozenset[int]] = frozenset({400, 413, 422})
_SUMMARY_FAILURE_STATE_MAX: Final[int] = 10_000

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


def sanitize_history(items: Sequence[HistoryItem], moderator: Moderator, *, drop: bool = False) -> list[HistoryItem]:
    """Gate #1（入力）で差し止めたユーザー発言の本文を、LLM に渡す履歴から取り除く。

    差し止めた発言も messages に保存される（BRIEF §2.5 の 4.）が、保存時の判定結果は列として持たない。
    Gate #1 は正規化テキストへの決定的な照合なので、保存済みの本文にもう一度かければ同じ判定になる。
    - drop=False: 本文をプレースホルダに置き換える（直後の定型返答は残し、会話の流れは保つ）
    - drop=True: その発言と直後のキャラ発言（定型返答）を取り除く（要約など、記憶として残る用途）
    """
    sanitized: list[HistoryItem] = []
    skip_reply = False
    for item in items:
        if skip_reply:
            skip_reply = False
            if item.sender_type == "character":
                continue
        if item.sender_type == "user" and moderator.check(item.body).flagged:
            if drop:
                skip_reply = True
                continue
            item = replace(item, body=MODERATED_PLACEHOLDER)  # noqa: PLW2901
        sanitized.append(item)
    return sanitized


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """記憶抽出（LLM 1回）の結果と、監査ログ（chat.response の extraction）用のメタデータ。"""

    candidates: list[MemoryCandidate]
    messages: list[ChatMessage] = field(default_factory=list)
    model: str | None = None
    latency_ms: int | None = None
    usage: dict[str, int] | None = None
    raw_output: str | None = None
    error: str | None = None

    @property
    def failed(self) -> bool:
        return self.error is not None

    def audit_payload(self, *, threshold: float, include_prompt: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "failed": self.failed,
            "error": self.error,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "usage": self.usage,
            "threshold": threshold,
            "candidates": [
                {"content": c.content, "importance": c.importance, "category": c.category} for c in self.candidates
            ],
        }
        if include_prompt:
            payload["prompt_messages"] = self.messages
            payload["raw_output"] = self.raw_output
        return payload


@dataclass(frozen=True, slots=True)
class SavedMemories:
    """save_candidates の結果（chat.response の監査用）。"""

    created: list[UUID] = field(default_factory=list)
    updated: list[UUID] = field(default_factory=list)
    skipped_user_edited: list[UUID] = field(default_factory=list)


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
    """要約 LLM の出力から要約文を取り出す。空なら ""。"""
    try:
        data = parse_json_object(text)
    except ValueError:
        # JSON で返らなかった場合は本文をそのまま要約とみなす
        return text.strip()[:SUMMARY_MAX_CHARS]
    summary = data.get("summary") if isinstance(data, dict) else None
    if not isinstance(summary, str):
        # JSON だが summary が無い・文字列でない（{"summary": ""} や別の形）→ JSON 文字列を要約として保存しない
        return ""
    return summary.strip()[:SUMMARY_MAX_CHARS]


def is_content_rejection(exc: LLMError) -> bool:
    """同じ入力で再試行しても通らない失敗か（プロバイダのコンテンツフィルタ・入力長超過など）。"""
    return exc.status_code in _CONTENT_REJECTION_STATUS


@dataclass(frozen=True, slots=True)
class SummaryChunk:
    """中期要約の1チャンク。"""

    cursor: datetime | None  # 読み込んだ時点の conversations.summary_cursor
    transcript: list[HistoryItem]  # 要約に渡す発言（差し止めたターンは除外済み・文字数上限内）
    last: HistoryItem  # このチャンクで扱った最後のメッセージ（カーソルをここまで進める）
    covered: int  # このチャンクで「要約済み」になるメッセージ数（除外したターンを含む）

    @property
    def excluded(self) -> int:
        return self.covered - len(self.transcript)


@dataclass(slots=True)
class _SummaryFailure:
    cursor: datetime | None
    failures: int
    retry_at: float


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
        moderator: Moderator,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._pool = pool
        self._embedder = embedder
        self._llm = llm
        self._prompts = prompts
        self._audit = audit
        self._moderator = moderator
        self._clock = clock
        self._summarizing: set[UUID] = set()
        # 会話ごとの中期要約の失敗状態（プロセス内。再起動で消えても次のチャットで再判定されるだけ）
        self._summary_failures: dict[UUID, _SummaryFailure] = {}

    # ------------------------------------------------------------------ 短期
    async def fetch_short_term(self, conn: Connection, conversation_id: UUID) -> list[HistoryItem]:
        """直近の会話（古い順）。差し止めた発言の本文はプレースホルダに置き換える。"""
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
        items = [
            HistoryItem(id=r["id"], sender_type=r["sender_type"], body=r["body"], created_at=r["created_at"])
            for r in rows
        ]
        return sanitize_history(items, self._moderator)

    # ------------------------------------------------------------------ 長期（検索）
    async def embed_query(
        self, text: str, *, user_id: UUID, character_id: UUID, conversation_id: UUID
    ) -> list[float] | None:
        """検索用の埋め込み。失敗・EMBEDDING_TIMEOUT_SECONDS 超過なら None（長期記憶の検索を省略してチャットは続ける）。

        失敗は audit `llm.error`（purpose=embedding_query）に残す（チャットは 200 のままなので、埋め込み障害で
        「何も思い出さない」状態が続いていることを監査ログ・アラートから分かるようにする）。
        ここでの TimeoutError は内側の asyncio.timeout のもので、呼び出し側の締め切り（CHAT_DEADLINE_SECONDS）の
        失効は CancelledError として外へ伝わる（asyncio.timeout は入れ子にしても区別される）。
        """
        timeout = self._settings.embedding_timeout_seconds
        try:
            async with asyncio.timeout(timeout):
                [vector] = await self._embedder.embed([text])
        except (EmbeddingError, TimeoutError) as exc:
            failure = embedding_failure_payload(exc, timeout_seconds=timeout)
            logger.error("query embedding failed; skipping long-term retrieval", extra={"fields": failure})
            await self.log_embedding_error(
                "embedding_query",
                failure,
                user_id=user_id,
                character_id=character_id,
                conversation_id=conversation_id,
            )
            return None
        return vector

    async def log_embedding_error(
        self, purpose: str, failure: dict[str, Any], *, user_id: UUID, character_id: UUID, **context: Any
    ) -> None:
        """埋め込みの失敗を audit `llm.error` に残す（ADR-0013: LLM・埋め込みの失敗。障害のアラートに使う）。"""
        await self._audit.log(
            "llm.error",
            user_id=user_id,
            character_id=character_id,
            payload={"purpose": purpose, **context, **failure, "embedding_model": self._embedder.model_name},
        )

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
        self,
        persona: Persona,
        *,
        history: Sequence[HistoryItem],
        user_message: str,
        now: datetime,
        user_id: UUID,
        character_id: UUID,
        conversation_id: UUID,
    ) -> ExtractionResult:
        """1回の LLM 呼び出しで記憶候補をまとめて抽出する。失敗しても例外は投げない（チャットを止めない）。

        失敗は audit `llm.error`（purpose=memory_extraction）に残す（「記憶すべきことが無かった」と区別するため）。
        """
        messages = self._prompts.extraction_messages(
            persona,
            history=history,
            user_message=user_message,
            now=now,
            threshold=self._settings.memory_importance_threshold,
        )
        request = LLMRequest(
            purpose="memory_extraction",
            messages=messages,
            temperature=0.0,
            max_tokens=EXTRACTION_MAX_TOKENS,
            json_mode=True,
            hints=MockHints(persona=persona, now=now, user_message=user_message, history=tuple(history)),
        )
        try:
            result = await self._llm.complete(request)
        except LLMError as exc:
            logger.error("memory extraction failed", extra={"fields": {"error": str(exc)}})
            await self.log_extraction_error(
                str(exc),
                user_id=user_id,
                character_id=character_id,
                conversation_id=conversation_id,
                status_code=exc.status_code,
                attempts=exc.attempts,
            )
            return ExtractionResult(candidates=[], messages=messages, error=str(exc))
        return ExtractionResult(
            candidates=parse_candidates(result.text),
            messages=messages,
            model=result.model,
            latency_ms=result.latency_ms,
            usage=result.usage,
            raw_output=result.text,
        )

    async def log_extraction_error(
        self,
        error: str,
        *,
        user_id: UUID,
        character_id: UUID,
        conversation_id: UUID,
        status_code: int | None = None,
        attempts: int | None = None,
    ) -> None:
        await self._audit.log(
            "llm.error",
            user_id=user_id,
            character_id=character_id,
            payload={
                "purpose": "memory_extraction",
                "conversation_id": conversation_id,
                "error": error,
                "status_code": status_code,
                "attempts": attempts,
            },
        )

    async def save_candidates(
        self,
        *,
        user_id: UUID,
        character_id: UUID,
        source_message_id: UUID | None,
        candidates: Sequence[MemoryCandidate],
    ) -> SavedMemories:
        """重要度が閾値以上の候補を保存する。近似重複は更新（ユーザー編集済みはスキップ）。"""
        threshold = self._settings.memory_importance_threshold
        selected = [c for c in candidates if c.importance >= threshold]
        if not selected:
            return SavedMemories()
        vectors = await self._embedder.embed([c.content for c in selected])
        created: list[tuple[UUID, MemoryCandidate]] = []
        updated: list[tuple[UUID, MemoryCandidate]] = []
        skipped: list[UUID] = []
        evicted: list[EvictedMemory] = []
        dropped_over_capacity = 0
        capacity = self._settings.memory_max_per_character
        async with self._pool.acquire() as conn:
            for candidate, vector in zip(selected, vectors, strict=True):
                literal = vector_literal(vector)
                async with conn.transaction():
                    # 同じペアの同時保存（重複判定 → 追加）を直列化する
                    await lock_pair(conn, user_id, character_id)
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
                    has_room, removed = await make_room(
                        conn, user_id=user_id, character_id=character_id, capacity=capacity
                    )
                    evicted.extend(removed)
                    if not has_room:
                        dropped_over_capacity += 1
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
        await self._log_evictions(evicted, user_id=user_id, character_id=character_id)
        if dropped_over_capacity:
            logger.warning(
                "extracted memories dropped: pair is at capacity and nothing is evictable",
                extra={
                    "fields": {
                        "user_id": str(user_id),
                        "character_id": str(character_id),
                        "dropped": dropped_over_capacity,
                        "capacity": capacity,
                    }
                },
            )
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
        return SavedMemories(
            created=[memory_id for memory_id, _ in created],
            updated=[memory_id for memory_id, _ in updated],
            skipped_user_edited=skipped,
        )

    async def _log_evictions(self, evicted: Sequence[EvictedMemory], *, user_id: UUID, character_id: UUID) -> None:
        """上限による入れ替えで削除した自動記憶を監査ログに残す（ユーザーが消したものと区別できるように）。"""
        for memory in evicted:
            await self._audit.log(
                "memory.delete",
                user_id=user_id,
                character_id=character_id,
                payload={
                    "memory_id": memory.id,
                    "source": "capacity_eviction",
                    "content": memory.content,
                    "importance": memory.importance,
                    "tags": memory.tags,
                    "capacity": self._settings.memory_max_per_character,
                    "was_user_edited": False,
                },
            )

    # ------------------------------------------------------------------ 中期（要約）
    async def maybe_summarize(
        self, *, conversation_id: UUID, user_id: UUID, character_id: UUID, persona: Persona, now: datetime
    ) -> UUID | None:
        """未要約メッセージが閾値を超えていれば、短期ウィンドウより古い部分を古い順にチャンクで要約する。

        BackgroundTask で動くため例外は外に出さない。戻り値は最後に作成した要約記憶の ID（無ければ None）。
        """
        if conversation_id in self._summarizing:
            return None
        failure = self._summary_failures.get(conversation_id)
        if failure is not None and self._clock() < failure.retry_at:
            # 直近に失敗している → バックオフ中は LLM を呼ばない（毎メッセージで失敗し続けない）
            return None
        self._summarizing.add(conversation_id)
        created: UUID | None = None
        try:
            for _ in range(MAX_SUMMARY_CHUNKS_PER_RUN):
                chunk = await self._next_summary_chunk(conversation_id, user_id, persona)
                if chunk is None:
                    break
                try:
                    progressed, memory_id = await self._summarize_chunk(
                        chunk,
                        conversation_id=conversation_id,
                        user_id=user_id,
                        character_id=character_id,
                        persona=persona,
                        now=now,
                    )
                except LLMError as exc:
                    await self._on_summary_failure(
                        exc, chunk, conversation_id=conversation_id, user_id=user_id, character_id=character_id
                    )
                    break
                self._summary_failures.pop(conversation_id, None)
                if memory_id is not None:
                    created = memory_id
                if not progressed:
                    break
            return created
        except Exception:
            # BackgroundTask 内で動くため、ここで必ずログに残す（チャット応答には影響させない）
            logger.exception(
                "mid-term summarization crashed", extra={"fields": {"conversation_id": str(conversation_id)}}
            )
            return created
        finally:
            self._summarizing.discard(conversation_id)

    async def _next_summary_chunk(self, conversation_id: UUID, user_id: UUID, persona: Persona) -> SummaryChunk | None:
        """要約すべきなら次のチャンク（古い順・文字数上限内）を返す。不要なら None。"""
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
                 limit $4
                """,
                conversation_id,
                cursor,
                boundary,
                SUMMARY_FETCH_LIMIT,
            )
        raw = [
            HistoryItem(id=r["id"], sender_type=r["sender_type"], body=r["body"], created_at=r["created_at"])
            for r in rows
        ]
        # 末尾が差し止めたユーザー発言なら、直後の定型返答（範囲外）と一緒に次回に回す（ターンごと除外するため）
        if raw and raw[-1].sender_type == "user" and self._moderator.check(raw[-1].body).flagged:
            raw.pop()
        if not raw:
            return None
        # 差し止めたターンは要約（= 以後ずっと注入される記憶）に入れない。カーソルは除外分も含めて進める
        transcript = sanitize_history(raw, self._moderator, drop=True)
        fitted = fit_transcript_prefix(transcript, persona)
        if fitted >= len(transcript):
            last = raw[-1]
        else:
            # 文字数上限に収まらない分は要約に含めず、カーソルも含めた分までしか進めない（次のチャンクで要約する）
            transcript = transcript[:fitted]
            last = transcript[-1]
        covered = sum(1 for item in raw if item.created_at <= last.created_at)
        return SummaryChunk(cursor=cursor, transcript=transcript, last=last, covered=covered)

    async def _summarize_chunk(
        self,
        chunk: SummaryChunk,
        *,
        conversation_id: UUID,
        user_id: UUID,
        character_id: UUID,
        persona: Persona,
        now: datetime,
    ) -> tuple[bool, UUID | None]:
        """1チャンクを要約して保存する。

        戻り値: (カーソルを進めたか, 作成した要約記憶の ID)。LLMError は呼び出し側（maybe_summarize）で扱う。
        """
        if not chunk.transcript:
            # 範囲がすべて差し止めたターン → 要約せずにカーソルだけ進める
            advanced = await self._advance_cursor(conversation_id, user_id, chunk)
            logger.info(
                "mid-term summary chunk had only moderated turns; cursor advanced",
                extra={"fields": {"conversation_id": str(conversation_id), "messages": chunk.covered}},
            )
            return advanced, None
        messages = self._prompts.summary_messages(persona, transcript=chunk.transcript)
        result = await self._llm.complete(
            LLMRequest(
                purpose="memory_summary",
                messages=messages,
                temperature=0.0,
                max_tokens=SUMMARY_MAX_TOKENS,
                json_mode=True,
                hints=MockHints(persona=persona, now=now, history=tuple(chunk.transcript)),
            )
        )
        summary = parse_summary(result.text)
        if not summary:
            # temperature 0 で空 → 同じ入力で再試行しても空になる。チャンクを飛ばして先へ進める
            await self._skip_chunk(
                chunk,
                conversation_id=conversation_id,
                user_id=user_id,
                character_id=character_id,
                error=f"empty summary from {result.model}",
                status_code=None,
                attempts=1,
                failures=1,
            )
            return True, None
        vector: list[float] | None
        embedding_failure: dict[str, Any] | None = None
        try:
            [vector] = await self._embedder.embed([summary])
        except EmbeddingError as exc:
            # 要約は最新2件が常に注入されるので、埋め込みが無くても使われる（類似検索の対象外になるだけ。
            # scripts/reembed_memories.py で後から埋め込める）。LLM の要約をやり直すより保存を優先する
            embedding_failure = embedding_failure_payload(exc)
            logger.error(
                "embedding for mid-term summary failed; saving without embedding",
                extra={"fields": {"conversation_id": str(conversation_id), **embedding_failure}},
            )
            vector = None
        evicted: list[EvictedMemory] = []
        async with self._pool.acquire() as conn, conn.transaction():
            current = await conn.fetchrow(
                "select summary_cursor from public.conversations where id = $1 and user_id = $2 for update",
                conversation_id,
                user_id,
            )
            if current is None or current["summary_cursor"] != chunk.cursor:
                # 他のワーカーが先に要約した
                return False, None
            # 要約は上限を超えても保存する（入れ替えられる自動記憶があれば入れ替える）
            _, evicted = await make_room(
                conn,
                user_id=user_id,
                character_id=character_id,
                capacity=self._settings.memory_max_per_character,
            )
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
                vector_literal(vector) if vector is not None else None,
                chunk.last.id,
            )
            await conn.execute(
                "update public.conversations set summary_cursor = $1 where id = $2 and user_id = $3",
                chunk.last.created_at,
                conversation_id,
                user_id,
            )
        await self._log_evictions(evicted, user_id=user_id, character_id=character_id)
        if embedding_failure is not None:
            await self.log_embedding_error(
                "memory_summary_embedding",
                embedding_failure,
                user_id=user_id,
                character_id=character_id,
                conversation_id=conversation_id,
                memory_id=memory_id,
            )
        payload: dict[str, Any] = {
            "memory_id": memory_id,
            "conversation_id": conversation_id,
            "summarized_messages": chunk.covered,
            "excluded_moderated_messages": chunk.excluded,
            "summary_cursor": chunk.last.created_at,
            "content": summary,
            "model": result.model,
            "latency_ms": result.latency_ms,
            "usage": result.usage,
            "embedded": vector is not None,
        }
        if self._settings.audit_log_prompts:
            # 他の生成（chat.response / 抽出 / comment.generate）と同じく、入力と生出力を残す（H6）
            payload["prompt_messages"] = messages
            payload["raw_output"] = result.text
        await self._audit.log("memory.summary", user_id=user_id, character_id=character_id, payload=payload)
        logger.info(
            "conversation summarized",
            extra={"fields": {"conversation_id": str(conversation_id), "messages": len(chunk.transcript)}},
        )
        return True, memory_id

    async def _on_summary_failure(
        self, exc: LLMError, chunk: SummaryChunk, *, conversation_id: UUID, user_id: UUID, character_id: UUID
    ) -> None:
        """要約の失敗: 内容による拒否か、同じチャンクで失敗が続いたら飛ばす。それ以外は指数バックオフで再試行。"""
        previous = self._summary_failures.get(conversation_id)
        failures = previous.failures + 1 if previous is not None and previous.cursor == chunk.cursor else 1
        if is_content_rejection(exc) or failures >= SUMMARY_MAX_ATTEMPTS_PER_CHUNK:
            self._summary_failures.pop(conversation_id, None)
            await self._skip_chunk(
                chunk,
                conversation_id=conversation_id,
                user_id=user_id,
                character_id=character_id,
                error=str(exc),
                status_code=exc.status_code,
                attempts=exc.attempts,
                failures=failures,
            )
            return
        delay = min(SUMMARY_RETRY_BASE_SECONDS * 2 ** (failures - 1), SUMMARY_RETRY_MAX_SECONDS)
        self._remember_failure(conversation_id, _SummaryFailure(chunk.cursor, failures, self._clock() + delay))
        logger.error(
            "mid-term summarization failed; will retry later",
            extra={
                "fields": {
                    "conversation_id": str(conversation_id),
                    "error": str(exc),
                    "failures": failures,
                    "retry_in_s": delay,
                }
            },
        )
        await self._audit.log(
            "llm.error",
            user_id=user_id,
            character_id=character_id,
            payload={
                "purpose": "memory_summary",
                "conversation_id": conversation_id,
                "error": str(exc),
                "status_code": exc.status_code,
                "attempts": exc.attempts,
                "failures": failures,
                "skipped": False,
                "retry_in_seconds": delay,
            },
        )

    async def _skip_chunk(
        self,
        chunk: SummaryChunk,
        *,
        conversation_id: UUID,
        user_id: UUID,
        character_id: UUID,
        error: str,
        status_code: int | None,
        attempts: int,
        failures: int,
    ) -> None:
        """要約できないチャンクを飛ばしてカーソルを進める（同じ範囲で LLM を呼び続けない）。"""
        advanced = await self._advance_cursor(conversation_id, user_id, chunk)
        logger.error(
            "mid-term summary chunk skipped",
            extra={
                "fields": {
                    "conversation_id": str(conversation_id),
                    "error": error,
                    "failures": failures,
                    "skipped_messages": chunk.covered,
                    "cursor_advanced": advanced,
                }
            },
        )
        await self._audit.log(
            "llm.error",
            user_id=user_id,
            character_id=character_id,
            payload={
                "purpose": "memory_summary",
                "conversation_id": conversation_id,
                "error": error,
                "status_code": status_code,
                "attempts": attempts,
                "failures": failures,
                "skipped": True,
                "skipped_messages": chunk.covered if advanced else 0,
                "summary_cursor": chunk.last.created_at if advanced else None,
            },
        )

    async def _advance_cursor(self, conversation_id: UUID, user_id: UUID, chunk: SummaryChunk) -> bool:
        """カーソルを chunk.last まで進める（読み込み後に他のワーカーが進めていたら何もしない）。"""
        async with self._pool.acquire() as conn, conn.transaction():
            current = await conn.fetchrow(
                "select summary_cursor from public.conversations where id = $1 and user_id = $2 for update",
                conversation_id,
                user_id,
            )
            if current is None or current["summary_cursor"] != chunk.cursor:
                return False
            await conn.execute(
                "update public.conversations set summary_cursor = $1 where id = $2 and user_id = $3",
                chunk.last.created_at,
                conversation_id,
                user_id,
            )
        return True

    def _remember_failure(self, conversation_id: UUID, failure: _SummaryFailure) -> None:
        if len(self._summary_failures) >= _SUMMARY_FAILURE_STATE_MAX:
            # 放置された会話の失敗状態で無制限に増えないよう、再試行時刻を過ぎたものを捨てる
            now = self._clock()
            for key in [k for k, v in self._summary_failures.items() if v.retry_at <= now]:
                del self._summary_failures[key]
        self._summary_failures[conversation_id] = failure
