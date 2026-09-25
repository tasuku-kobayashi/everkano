from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.services.memory import MODERATED_PLACEHOLDER, ExtractionResult, sanitize_history
from app.services.moderation import Moderator
from app.services.types import HistoryItem, MemoryCandidate, SenderType

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
