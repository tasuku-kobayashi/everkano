"""中期要約（M7 / ADR-0009・0028）。ジョブ `memory.summarize`（会話ごとにデバウンス）から呼ばれる。

- `conversations.summary_cursor` より新しいメッセージが `summary_trigger_message_count` を超えたら、
  短期ウィンドウ（直近 `short_term_message_limit` 件）より古い未要約分を、古い順に会話ログの上限
  （TRANSCRIPT_MAX_CHARS）に収まる分ずつ要約する（kind = summary, tags = {summary}）。
- カーソルは実際に要約に含めたメッセージまでしか進めない。1 回の処理で最大 MAX_SUMMARY_CHUNKS_PER_RUN チャンク。
- 失敗は会話ごとに指数バックオフ（時刻は呼び出し側の now。評価ハーネスの時間の早送りでも再現できる）。
  同じチャンクで失敗が続く・内容で拒否される（4xx）場合はそのチャンクを飛ばしてカーソルを進める。
- Gate #1 で差し止めたターンは要約に入れない（sanitize_history(drop=True)）。設定・関係・評価を書き換えようとする
  発言（記憶経由の注入, guard.py）も入れない。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final
from uuid import UUID

import asyncpg

from app.core.db import Pool, vector_literal
from app.core.logging import get_logger
from app.engine.memory.analysis import AnalysisOutputError, load_json_object
from app.engine.memory.capacity import EvictedMemory, log_evictions, make_room
from app.engine.memory.config import MemoryConfig
from app.engine.memory.embedding import EmbeddingClient, EmbeddingError, embedding_failure_payload
from app.engine.memory.guard import drop_injection_history
from app.engine.memory.text import fit_transcript_prefix, format_date_ja, render_transcript, sanitize_history
from app.engine.types import JST
from app.services.audit import AuditLogger
from app.services.characters import to_numeric
from app.services.llm import LLMClient, LLMError, LLMRequest
from app.services.moderation import Moderator
from app.services.persona import Persona
from app.services.prompt import ChatMessage, PromptTemplate, PromptTemplateError, parse_template
from app.services.types import MEMORY_TAG_SUMMARY, HistoryItem

logger = get_logger("memory.summary")

SUMMARY_TEMPLATE: Final[str] = "memory_summary"
SUMMARY_IMPORTANCE: Final[float] = 0.7
SUMMARY_MAX_CHARS: Final[int] = 900
SUMMARY_MAX_TOKENS: Final[int] = 800
# 1回に DB から読む未要約メッセージの上限（この中から文字数上限に収まる分だけを1チャンクとして要約する）
SUMMARY_FETCH_LIMIT: Final[int] = 200
# 1回の処理で要約するチャンク数の上限（溜まった分は以後のジョブで少しずつ消化する）
MAX_SUMMARY_CHUNKS_PER_RUN: Final[int] = 3
# 同じチャンクでこの回数失敗したら、そのチャンクは飛ばしてカーソルを進める
SUMMARY_MAX_ATTEMPTS_PER_CHUNK: Final[int] = 3
# 失敗後の再試行までの待ち（指数バックオフ）
SUMMARY_RETRY_BASE_SECONDS: Final[float] = 60.0
SUMMARY_RETRY_MAX_SECONDS: Final[float] = 3600.0
# 内容が原因で拒否されたとみなす LLM の HTTP ステータス（同じ内容で再試行しても通らない）
_CONTENT_REJECTION_STATUS: Final[frozenset[int]] = frozenset({400, 413, 422})
_SUMMARY_FAILURE_STATE_MAX: Final[int] = 10_000


def parse_summary(text: str) -> str:
    """要約 LLM の出力から要約文を取り出す。空なら ""。"""
    try:
        data = load_json_object(text)
    except AnalysisOutputError:
        # JSON で返らなかった場合は本文をそのまま要約とみなす
        return text.strip()[:SUMMARY_MAX_CHARS]
    summary = data.get("summary") if isinstance(data, dict) else None
    if not isinstance(summary, str):
        # JSON だが summary が無い・文字列でない → JSON 文字列を要約として保存しない
        return ""
    return summary.strip()[:SUMMARY_MAX_CHARS]


def is_content_rejection(exc: LLMError) -> bool:
    """同じ入力で再試行しても通らない失敗か（プロバイダのコンテンツフィルタ・入力長超過など）。"""
    return exc.status_code in _CONTENT_REJECTION_STATUS


def load_summary_template(prompts_dir: Path) -> PromptTemplate:
    path = prompts_dir / f"{SUMMARY_TEMPLATE}.ja.txt"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptTemplateError(f"{path}: 読み込めません: {exc}") from exc
    template = parse_template(SUMMARY_TEMPLATE, text)
    if "conversation" not in template.placeholders:
        raise PromptTemplateError(f"{SUMMARY_TEMPLATE}: 必須プレースホルダ {{conversation}} がありません")
    return template


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
    retry_at: datetime


PersonaLookup = Callable[[UUID], Awaitable[Persona | None]]


class MemorySummarizer:
    def __init__(
        self,
        *,
        pool: Pool,
        llm: LLMClient,
        embedder: EmbeddingClient,
        audit: AuditLogger,
        moderator: Moderator,
        config: MemoryConfig,
        template: PromptTemplate,
        persona_for: PersonaLookup,
    ) -> None:
        self._pool = pool
        self._llm = llm
        self._embedder = embedder
        self._audit = audit
        self._moderator = moderator
        self._config = config
        self._template = template
        self._persona_for = persona_for
        self._summarizing: set[UUID] = set()
        # 会話ごとの失敗状態（プロセス内。再起動で消えても次のジョブで再判定されるだけ）
        self._failures: dict[UUID, _SummaryFailure] = {}

    async def maybe_summarize(
        self, *, conversation_id: UUID, user_id: UUID, character_id: UUID, now: datetime
    ) -> UUID | None:
        """未要約メッセージが閾値を超えていれば要約する。例外は外に出さない。戻り値は最後に作成した要約の ID。"""
        if conversation_id in self._summarizing:
            return None
        failure = self._failures.get(conversation_id)
        if failure is not None and now < failure.retry_at:
            # 直近に失敗している → バックオフ中は LLM を呼ばない
            return None
        self._summarizing.add(conversation_id)
        created: UUID | None = None
        try:
            persona = await self._persona_for(character_id)
            if persona is None:
                return None
            for _ in range(MAX_SUMMARY_CHUNKS_PER_RUN):
                chunk = await self._next_chunk(conversation_id, user_id, persona)
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
                    await self._on_failure(
                        exc, chunk, conversation_id=conversation_id, user_id=user_id, character_id=character_id, now=now
                    )
                    break
                self._failures.pop(conversation_id, None)
                if memory_id is not None:
                    created = memory_id
                if not progressed:
                    break
            return created
        except asyncpg.ForeignKeyViolationError:
            # 要約の途中でユーザー・会話が削除された（退会・テストの後片付け）→ 何もしない
            logger.info(
                "conversation vanished during summarization",
                extra={"fields": {"conversation_id": str(conversation_id)}},
            )
            return created
        except Exception:
            # ジョブの中で動くため、ここで必ずログに残す（会話の応答には影響させない）
            logger.exception(
                "mid-term summarization crashed", extra={"fields": {"conversation_id": str(conversation_id)}}
            )
            return created
        finally:
            self._summarizing.discard(conversation_id)

    def messages(self, persona: Persona, transcript: list[HistoryItem]) -> list[ChatMessage]:
        # 相対的な日付（「来週」など）を要約で具体的な時期に直せるよう、範囲の日付を先頭に添える
        period = ""
        if transcript:
            first, last = (format_date_ja(transcript[i].created_at.astimezone(JST).date()) for i in (0, -1))
            period = f"（この範囲の会話: {first}〜{last}、日本時間）\n"
        return self._template.render(
            {"name": persona.name, "conversation": period + render_transcript(transcript, persona.name)}
        )

    async def _next_chunk(self, conversation_id: UUID, user_id: UUID, persona: Persona) -> SummaryChunk | None:
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
            if unsummarized <= self._config.summary_trigger_message_count:
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
                self._config.short_term_message_limit - 1,
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
        # 末尾が差し止めたユーザー発言なら、直後の定型返答（範囲外）と一緒に次回に回す
        if raw and raw[-1].sender_type == "user" and self._moderator.check(raw[-1].body).flagged:
            raw.pop()
        if not raw:
            return None
        transcript = sanitize_history(raw, self._moderator, drop=True)
        # 設定・関係・評価を書き換えようとする発言は要約にも残さない（記憶経由の注入の防止, guard.py）
        transcript, _ = drop_injection_history(transcript)
        fitted = fit_transcript_prefix(transcript, persona.name)
        if fitted >= len(transcript):
            last = raw[-1]
        else:
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
        if not chunk.transcript:
            advanced = await self._advance_cursor(conversation_id, user_id, chunk)
            return advanced, None
        messages = self.messages(persona, chunk.transcript)
        result = await self._llm.complete(
            LLMRequest(
                purpose="memory_summary",
                messages=messages,
                temperature=0.0,
                max_tokens=SUMMARY_MAX_TOKENS,
                json_mode=True,
                model=self._config.summary_model,
                mock_context={
                    "transcript": [{"sender": h.sender_type, "body": h.body} for h in chunk.transcript],
                },
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
                now=now,
            )
            return True, None
        vector: list[float] | None
        embedding_failure: dict[str, Any] | None = None
        try:
            [vector] = await self._embedder.embed([summary])
        except EmbeddingError as exc:
            # 最新の要約は常に注入されるので、埋め込みが無くても使われる。LLM の要約をやり直すより保存を優先する
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
                return False, None  # 他のワーカーが先に要約した
            _, evicted = await make_room(
                conn, user_id=user_id, character_id=character_id, capacity=self._config.max_per_character
            )
            memory_id: UUID = await conn.fetchval(
                """
                insert into public.memories
                  (user_id, character_id, kind, content, importance, tags, embedding, source_message_id,
                   source_conversation_id, is_user_edited, created_at, updated_at)
                values ($1, $2, 'summary', $3, $4, $5, $6::text::extensions.vector, $7, $8, false, $9, $9)
                returning id
                """,
                user_id,
                character_id,
                summary,
                to_numeric(SUMMARY_IMPORTANCE),
                [MEMORY_TAG_SUMMARY],
                vector_literal(vector) if vector is not None else None,
                chunk.last.id,
                conversation_id,
                now,
            )
            await conn.execute(
                "update public.conversations set summary_cursor = $1 where id = $2 and user_id = $3",
                chunk.last.created_at,
                conversation_id,
                user_id,
            )
        await log_evictions(
            self._audit,
            evicted,
            user_id=user_id,
            character_id=character_id,
            capacity=self._config.max_per_character,
            now=now,
        )
        if embedding_failure is not None:
            await self._audit.log(
                "llm.error",
                user_id=user_id,
                character_id=character_id,
                payload={
                    "purpose": "memory_summary_embedding",
                    "conversation_id": conversation_id,
                    "memory_id": memory_id,
                    **embedding_failure,
                    "embedding_model": self._embedder.model_name,
                },
                at=now,
            )
        payload: dict[str, Any] = {
            "memory_id": memory_id,
            "conversation_id": conversation_id,
            "summarized_messages": chunk.covered,
            "excluded_moderated_messages": chunk.excluded,
            "summary_cursor": chunk.last.created_at,
            "content": summary,
            "purpose": "memory_summary",
            "model": result.model,
            "latency_ms": result.latency_ms,
            "usage": result.usage,
            "embedded": vector is not None,
        }
        if self._config.audit_log_prompts:
            payload["prompt_messages"] = messages
            payload["raw_output"] = result.text
        await self._audit.log("memory.summary", user_id=user_id, character_id=character_id, payload=payload, at=now)
        return True, memory_id

    async def _on_failure(
        self,
        exc: LLMError,
        chunk: SummaryChunk,
        *,
        conversation_id: UUID,
        user_id: UUID,
        character_id: UUID,
        now: datetime,
    ) -> None:
        previous = self._failures.get(conversation_id)
        failures = previous.failures + 1 if previous is not None and previous.cursor == chunk.cursor else 1
        if is_content_rejection(exc) or failures >= SUMMARY_MAX_ATTEMPTS_PER_CHUNK:
            self._failures.pop(conversation_id, None)
            await self._skip_chunk(
                chunk,
                conversation_id=conversation_id,
                user_id=user_id,
                character_id=character_id,
                error=str(exc),
                status_code=exc.status_code,
                attempts=exc.attempts,
                failures=failures,
                now=now,
            )
            return
        delay = min(SUMMARY_RETRY_BASE_SECONDS * 2 ** (failures - 1), SUMMARY_RETRY_MAX_SECONDS)
        self._remember_failure(
            conversation_id, _SummaryFailure(chunk.cursor, failures, now + timedelta(seconds=delay)), now
        )
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
            at=now,
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
        now: datetime,
    ) -> None:
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
            at=now,
        )

    async def _advance_cursor(self, conversation_id: UUID, user_id: UUID, chunk: SummaryChunk) -> bool:
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

    def _remember_failure(self, conversation_id: UUID, failure: _SummaryFailure, now: datetime) -> None:
        if len(self._failures) >= _SUMMARY_FAILURE_STATE_MAX:
            for key in [k for k, v in self._failures.items() if v.retry_at <= now]:
                del self._failures[key]
        self._failures[conversation_id] = failure
