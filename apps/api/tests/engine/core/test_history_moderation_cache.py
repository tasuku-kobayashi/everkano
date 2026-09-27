"""履歴の Gate #1 の判定の記憶（ModerationVerdictCache）。

/chat のたびに同じ履歴を判定し直さない（新しい行だけ判定する）・本文が変われば判定し直す・上限で古いものから消す。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime

from app.engine.context_assembler import MODERATED_PLACEHOLDER, ModerationVerdictCache, sanitize_history
from app.services.moderation import ModerationResult
from app.services.types import HistoryItem

NOW = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)


class _CountingModerator:
    """「NG」を含む本文を差し止める（呼ばれた回数を数える）。"""

    def __init__(self) -> None:
        self.calls = 0

    def check(self, text: str, *, extra_ng_words: Sequence[str] = (), block_links: bool = False) -> ModerationResult:
        self.calls += 1
        flagged = "NG" in text
        return ModerationResult(flagged=flagged, categories=["test"] if flagged else [], matched_terms=[])


def _item(sender: str, body: str) -> HistoryItem:
    return HistoryItem(id=uuid.uuid4(), sender_type=sender, body=body, created_at=NOW)


def test_verdicts_are_cached_per_message_and_only_new_rows_are_checked() -> None:
    moderator = _CountingModerator()
    cache = ModerationVerdictCache()
    items = [_item("user", "こんにちは"), _item("character", "やあ"), _item("user", "NG な発言")]

    first = sanitize_history(items, moderator, cache)
    assert moderator.calls == 2  # キャラの発言は判定しない
    assert first[0].body == "こんにちは"
    assert first[1].body == "やあ"
    assert first[2].body == MODERATED_PLACEHOLDER

    # 同じ履歴をもう一度 → 判定しない（結果は同じ）
    assert sanitize_history(items, moderator, cache) == first
    assert moderator.calls == 2

    # 新しい行だけ判定する
    newer = [*items, _item("user", "新しい発言")]
    assert sanitize_history(newer, moderator, cache)[3].body == "新しい発言"
    assert moderator.calls == 3

    # 同じ ID でも本文が変われば判定し直す
    edited = replace(items[0], body="NG に変わった")
    assert sanitize_history([edited], moderator, cache)[0].body == MODERATED_PLACEHOLDER
    assert moderator.calls == 4

    # cache を渡さなければ従来どおり毎回判定する
    sanitize_history(items, moderator)
    assert moderator.calls == 6


def test_cache_is_bounded_and_drops_the_least_recently_used() -> None:
    moderator = _CountingModerator()
    cache = ModerationVerdictCache(max_entries=2)
    a, b, c = (_item("user", text) for text in ("a", "b", "c"))
    sanitize_history([a, b], moderator, cache)
    assert len(cache) == 2
    sanitize_history([a], moderator, cache)  # a を最近使った
    sanitize_history([c], moderator, cache)  # 上限 → 使われていない b が消える
    assert len(cache) == 2
    assert cache.get(b) is None
    assert cache.get(a) is False
    assert cache.get(c) is False
    assert moderator.calls == 3
