"""検索のランキング（M5）の単体テスト。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.engine.memory.config import MemoryConfig, RankingWeights
from app.engine.memory.ranking import PINNED_SCORE, Candidate, lexical_overlap, rank, recency, score

NOW = datetime(2026, 9, 26, 3, 0, tzinfo=UTC)
CONFIG = MemoryConfig()


def cand(
    content: str,
    *,
    kind: str = "fact",
    similarity: float | None = 0.5,
    importance: float = 0.7,
    age_days: float = 0,
    referenced_days_ago: float | None = None,
    lexical: float = 0.0,
) -> Candidate:
    return Candidate(
        id=uuid.uuid4(),
        kind=kind,
        content=content,
        importance=importance,
        tags=(),
        created_at=NOW - timedelta(days=age_days),
        last_referenced_at=NOW - timedelta(days=referenced_days_ago) if referenced_days_ago is not None else None,
        is_user_edited=False,
        similarity=similarity,
        lexical=lexical,
    )


def test_recency_half_life_and_reference_refresh() -> None:
    assert recency(created_at=NOW, last_referenced_at=None, now=NOW, half_life_days=7) == 1.0
    assert recency(created_at=NOW - timedelta(days=7), last_referenced_at=None, now=NOW, half_life_days=7) == (
        pytest.approx(0.5)
    )
    # 最後に参照された日時から数える（参照された記憶は忘れにくい）
    assert recency(
        created_at=NOW - timedelta(days=70), last_referenced_at=NOW - timedelta(days=7), now=NOW, half_life_days=7
    ) == pytest.approx(0.5)
    # 半減期 0 以下 = 減衰しない / 未来の日時は 1
    assert recency(created_at=NOW - timedelta(days=999), last_referenced_at=None, now=NOW, half_life_days=0) == 1.0
    assert recency(created_at=NOW + timedelta(days=1), last_referenced_at=None, now=NOW, half_life_days=7) == 1.0


def test_score_formula() -> None:
    weights = RankingWeights(similarity=0.5, importance=0.3, recency=0.2, lexical=0.1)
    c = cand("x", similarity=0.8, importance=0.6, age_days=0, lexical=0.5)
    value = score(c, now=NOW, weights=weights, half_life_days={"fact": 10}, kind_boost={"fact": 0.05})
    assert value == pytest.approx(0.5 * 0.8 + 0.3 * 0.6 + 0.2 * 1.0 + 0.1 * 0.5 + 0.05)
    # 類似度・重要度は 0〜1 に丸める
    wild = cand("x", similarity=1.7, importance=-1)
    assert score(wild, now=NOW, weights=weights, half_life_days={}, kind_boost={}) == pytest.approx(0.5 + 0.2)


def test_emotion_decays_faster_than_facts() -> None:
    fact = cand("ユーザーは銀行で働いている", kind="fact", age_days=60, similarity=0.4)
    emotion = cand("ユーザーは落ち込んでいた", kind="emotion", age_days=60, similarity=0.4)
    s_fact = score(fact, now=NOW, weights=CONFIG.weights, half_life_days=CONFIG.half_life_days, kind_boost={})
    s_emotion = score(emotion, now=NOW, weights=CONFIG.weights, half_life_days=CONFIG.half_life_days, kind_boost={})
    assert s_fact > s_emotion


def test_similarity_dominates_and_threshold_filters_noise() -> None:
    relevant = cand("ユーザーは大阪に出張する", similarity=0.6, importance=0.6)
    important_but_unrelated = cand("ユーザーは妹がいる", similarity=0.2, importance=1.0)
    noise = cand("ユーザーは雨が苦手", similarity=0.05, importance=0.9)
    strict = MemoryConfig(fill_below_threshold=False)
    ranked = rank([important_but_unrelated, noise, relevant], pinned=[], now=NOW, config=strict)
    assert [r.candidate.id for r in ranked] == [relevant.id, important_but_unrelated.id]
    # 枠が余っていれば、下限未満の記憶は関係の深い記憶の後ろに入る（記憶が少ないうちは全部見せる）
    filled = rank([important_but_unrelated, noise, relevant], pinned=[], now=NOW, config=CONFIG)
    assert [r.candidate.id for r in filled] == [relevant.id, important_but_unrelated.id, noise.id]
    tight = rank([important_but_unrelated, noise, relevant], pinned=[], now=NOW, config=CONFIG, max_items=2)
    assert noise.id not in {r.candidate.id for r in tight}


def test_lexical_match_rescues_low_similarity() -> None:
    assert lexical_overlap("ミケは元気？", "ユーザーは猫のミケを飼っている") == 0.5  # 「ミケ」は一致、「元気」は不一致
    assert lexical_overlap("今日はどうだった？", "ユーザーは今日カフェに行った") == 0.0  # 時間表現は数えない
    exact_name = cand("ユーザーは猫のミケを飼っている", similarity=0.1, lexical=1.0)
    ranked = rank([exact_name], pinned=[], now=NOW, config=CONFIG)
    assert [r.candidate.id for r in ranked] == [exact_name.id]


def test_pinned_first_then_limits() -> None:
    pinned = [cand("ユーザーは「たっくん」と呼ばれたい", kind="relationship", similarity=None)]
    others = [cand(f"記憶{i}", similarity=0.9 - i * 0.01) for i in range(20)]
    ranked = rank(others, pinned=pinned, now=NOW, config=CONFIG)
    assert ranked[0].pinned is True
    assert ranked[0].score == PINNED_SCORE
    assert len(ranked) == CONFIG.max_memories
    assert [r.candidate.content for r in ranked[1:4]] == ["記憶0", "記憶1", "記憶2"]


def test_char_budget_skips_long_items_but_keeps_short_ones() -> None:
    config = MemoryConfig(memory_chars_budget=30)
    long = cand("長い" * 20, similarity=0.9)
    short = cand("短い記憶", similarity=0.5)
    ranked = rank([long, short], pinned=[], now=NOW, config=config)
    assert [r.candidate.id for r in ranked] == [short.id]
    assert rank([long, short], pinned=[], now=NOW, config=config, max_chars=100, max_items=1)[0].candidate.id == long.id


def test_without_similarity_ranks_by_importance_and_recency() -> None:
    old = cand("古いけど大事", similarity=None, importance=0.9, age_days=400)
    fresh = cand("新しい", similarity=None, importance=0.6, age_days=0)
    ranked = rank([old, fresh], pinned=[], now=NOW, config=CONFIG, use_similarity=False)
    assert {r.candidate.id for r in ranked} == {old.id, fresh.id}


def test_excluded_ids_are_skipped() -> None:
    a, b = cand("a"), cand("b")
    ranked = rank([a, b], pinned=[], now=NOW, config=CONFIG, exclude_ids=frozenset({a.id}))
    assert [r.candidate.id for r in ranked] == [b.id]
