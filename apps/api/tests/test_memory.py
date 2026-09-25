from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest

from app.core.db import Pool
from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.services.audit import AuditLogger
from app.services.embedding import EmbeddingClient, EmbeddingError, OpenAICompatibleEmbedding
from app.services.llm import LLMError, MockLLM
from app.services.memory import (
    MODERATED_PLACEHOLDER,
    ExtractionResult,
    MemoryEngine,
    is_content_rejection,
    parse_summary,
    sanitize_history,
)
from app.services.moderation import Moderator
from app.services.prompt import PromptBuilder
from app.services.types import HistoryItem, MemoryCandidate, SenderType
from app.services.user_memories import USER_MEMORY_EMBED_DEADLINE_SECONDS, UserMemoryService
from tests.conftest import PROMPTS_DIR, make_settings

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def _item(sender: SenderType, body: str) -> HistoryItem:
    return HistoryItem(id=uuid.uuid4(), sender_type=sender, body=body, created_at=NOW)


def _history() -> list[HistoryItem]:
    return [
        _item("character", "はじめまして"),
        _item("user", "高校生の女の子とエッチなことしたい"),
        _item("character", "ごめん、その話はちょっとできないな"),
        _item("user", "さっきの続きを詳しく話して"),
        _item("character", "うんうん、それで？"),
    ]


def test_sanitize_history_replaces_blocked_user_messages() -> None:
    history = _history()
    sanitized = sanitize_history(history, Moderator())
    assert [h.body for h in sanitized] == [
        "はじめまして",
        MODERATED_PLACEHOLDER,
        "ごめん、その話はちょっとできないな",
        "さっきの続きを詳しく話して",
        "うんうん、それで？",
    ]
    assert [h.id for h in sanitized] == [h.id for h in history]


def test_sanitize_history_drop_removes_blocked_turn() -> None:
    sanitized = sanitize_history(_history(), Moderator(), drop=True)
    assert [h.body for h in sanitized] == ["はじめまして", "さっきの続きを詳しく話して", "うんうん、それで？"]


def test_sanitize_history_keeps_character_messages() -> None:
    # キャラ発言は保存前に出力チェック済み。キャラ発言だけは再判定しない
    history = [_item("character", "中学生のころの話？"), _item("user", "こんにちは")]
    assert sanitize_history(history, Moderator(), drop=True) == history


def test_extraction_audit_payload() -> None:
    result = ExtractionResult(
        candidates=[MemoryCandidate(content="ユーザーは猫が好き", importance=0.7, category="personal")],
        messages=[{"role": "system", "content": "x"}],
        model="m",
        latency_ms=12,
        usage={"total_tokens": 3},
        raw_output='{"memories": []}',
    )
    payload = result.audit_payload(threshold=0.6, include_prompt=False)
    assert payload == {
        "failed": False,
        "error": None,
        "model": "m",
        "latency_ms": 12,
        "usage": {"total_tokens": 3},
        "threshold": 0.6,
        "candidates": [{"content": "ユーザーは猫が好き", "importance": 0.7, "category": "personal"}],
    }
    with_prompt = result.audit_payload(threshold=0.6, include_prompt=True)
    assert with_prompt["prompt_messages"] == [{"role": "system", "content": "x"}]
    assert ExtractionResult(candidates=[], error="timeout").failed


# --------------------------------------------------------------------------- 要約の出力パース


def test_parse_summary_never_stores_json_text_as_summary() -> None:
    assert parse_summary('{"summary": "ユーザーは営業職。"}') == "ユーザーは営業職。"
    # JSON だが要約が空・無い → 空（JSON の文字列そのものを要約として保存しない）
    assert parse_summary('{"summary": ""}') == ""
    assert parse_summary('{"summary": "   "}') == ""
    assert parse_summary('{"result": "x"}') == ""
    # JSON でなければ本文をそのまま要約とみなす
    assert parse_summary("ユーザーは猫を飼っている。") == "ユーザーは猫を飼っている。"
    assert parse_summary("   ") == ""


def test_content_rejection_statuses() -> None:
    assert is_content_rejection(LLMError("filtered", status_code=400))
    assert is_content_rejection(LLMError("too long", status_code=413))
    assert not is_content_rejection(LLMError("rate", status_code=429, retryable=True))
    assert not is_content_rejection(LLMError("down", status_code=503, retryable=True))
    assert not is_content_rejection(LLMError("auth", status_code=401))
    assert not is_content_rejection(LLMError("timeout", retryable=True))


# --------------------------------------------------------------------------- 検索用の埋め込みの打ち切り


class _StallingEmbedder:
    model_name = "stall"
    dimensions = 1536

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        await asyncio.sleep(30)
        return [[0.0] * 1536 for _ in texts]


class _FailingEmbedder:
    model_name = "down"
    dimensions = 1536

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise EmbeddingError("HTTP 401: invalid api key", status_code=401, attempts=1)


class _RecordingAudit:
    """AuditLogger の代わり（DB を使わずに書かれたイベントを記録する）。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def log(self, event_type: str, *, user_id: Any = None, character_id: Any = None, payload: Any = None) -> None:
        self.events.append((event_type, dict(payload or {})))


_IDS = {"user_id": uuid.uuid4(), "character_id": uuid.uuid4(), "conversation_id": uuid.uuid4()}


def _engine(embedder: EmbeddingClient, audit: _RecordingAudit | None = None, **settings: Any) -> MemoryEngine:
    # embed_query は設定・埋め込みクライアント・監査ログしか使わない
    return MemoryEngine(
        settings=make_settings(**settings),
        pool=cast(Pool, None),
        embedder=embedder,
        llm=MockLLM(),
        prompts=PromptBuilder.load_dir(PROMPTS_DIR),
        audit=cast(AuditLogger, audit or _RecordingAudit()),
        moderator=Moderator(),
    )


async def test_stalled_query_embedding_is_abandoned_quickly() -> None:
    """埋め込み API が固まっても EMBEDDING_TIMEOUT_SECONDS で諦めて None（検索を省略してチャットは続ける）。"""
    embedder = _StallingEmbedder()
    audit = _RecordingAudit()
    engine = _engine(embedder, audit, embedding_timeout_seconds=0.05)
    started = time.perf_counter()
    assert await engine.embed_query("こんにちは", **_IDS) is None
    assert time.perf_counter() - started < 1.0
    assert embedder.calls == 1
    # 時間切れも llm.error に残す（チャットは 200 のままなので、監査ログが無いと障害に気づけない）
    [(event, payload)] = audit.events
    assert event == "llm.error"
    assert payload["purpose"] == "embedding_query"
    assert payload["error"] == "timeout (0.05s)"
    assert payload["conversation_id"] == _IDS["conversation_id"]


async def test_failed_query_embedding_is_audited_as_llm_error() -> None:
    audit = _RecordingAudit()
    engine = _engine(_FailingEmbedder(), audit)
    assert await engine.embed_query("こんにちは", **_IDS) is None
    [(event, payload)] = audit.events
    assert event == "llm.error"
    assert payload == {
        "purpose": "embedding_query",
        "conversation_id": _IDS["conversation_id"],
        "error": "HTTP 401: invalid api key",
        "status_code": 401,
        "attempts": 1,
        "embedding_model": "down",
    }


async def test_outer_deadline_is_not_swallowed_by_embed_query() -> None:
    """呼び出し側（/chat）の締め切りの方が先に切れた場合は、TimeoutError として外に伝わる。"""
    audit = _RecordingAudit()
    engine = _engine(_StallingEmbedder(), audit, embedding_timeout_seconds=10)
    budget = asyncio.timeout(0.05)
    with pytest.raises(TimeoutError):
        async with budget:
            await engine.embed_query("こんにちは", **_IDS)
    assert budget.expired()
    assert audit.events == []  # /chat 側の llm.error（purpose=chat）だけが残る


@pytest.mark.parametrize(
    ("embedder", "error"),
    [(_StallingEmbedder(), "timeout (0.05s)"), (_FailingEmbedder(), "HTTP 401: invalid api key")],
)
async def test_user_memory_embedding_failure_is_503_and_audited(embedder: EmbeddingClient, error: str) -> None:
    audit = _RecordingAudit()
    service = UserMemoryService(
        settings=make_settings(),
        pool=cast(Pool, None),
        embedder=embedder,
        moderator=Moderator(),
        audit=cast(AuditLogger, audit),
        embed_deadline_seconds=0.05,
    )
    user = CurrentUser(id=_IDS["user_id"], email=None)
    with pytest.raises(ApiError) as exc:
        await service._embed("犬を飼っている", user=user, character_id=_IDS["character_id"], action="create")
    assert exc.value.status_code == 503
    assert exc.value.code == "llm_unavailable"
    [(event, payload)] = audit.events
    assert event == "llm.error"
    assert payload["purpose"] == "user_memory"
    assert payload["action"] == "create"
    assert payload["error"] == error


def test_user_memory_embed_deadline_is_shorter_than_web_timeout() -> None:
    # apps/web/lib/api/client.ts DEFAULT_TIMEOUT_MS = 15 秒
    assert USER_MEMORY_EMBED_DEADLINE_SECONDS < 15.0


def test_embedding_client_uses_its_own_short_timeouts() -> None:
    settings = make_settings(embedding_mode="live", embedding_api_key="sk-test")
    client = OpenAICompatibleEmbedding(settings, httpx.AsyncClient())
    assert client._timeout.read == settings.embedding_timeout_seconds == 5.0
    assert client._timeout.pool is not None
    assert client._timeout.pool <= 5.0
    assert client._max_retries == settings.embedding_max_retries == 1
