"""記憶の検索のランキング（M5）。DB を使わない純粋な関数（単体テストで検証する）。

総合点 = w_sim·類似度 + w_imp·重要度 + w_rec·新しさ + w_lex·語の一致 + 種類の加点
- 類似度: 問い合わせ（ユーザーの発言）とのコサイン類似度（0〜1 に丸める）
- 語の一致: 問い合わせの内容語（漢字・カタカナ・英数字の 2 文字以上の連続）のうち記憶に含まれる割合
  （固有名詞「ミケ」「大阪」などの完全一致を拾う。埋め込みの弱点を補うハイブリッド検索）
- 新しさ: 0.5 ^ (経過日数 / 種類ごとの半減期)。経過日数は「最後にプロンプトへ入れた日時」と作成日時の新しい方から数える
  （Generative Agents の recency。参照された記憶は忘れにくくなる）
- 重要度: 抽出時に LLM が付けた 0〜1（Generative Agents の importance）

常に入れる記憶（呼び方・最新の要約・重要な事実）は、総合点に関係なく先頭に置く。残りを総合点の順に、
件数（max_memories）と文字数（memory_chars_budget）の上限まで入れる。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from app.engine.memory.config import MemoryConfig, RankingWeights

# 常に入れる記憶の総合点（並べ替えで先頭に来るようにする。監査ログで区別できる値）
PINNED_SCORE = 10.0
_CONTENT_RUN: Final = re.compile(r"[\u4e00-\u9fff\u3005]{2,}|[\u30a1-\u30fa\u30fc]{2,}|[A-Za-z0-9]{2,}")
# 語の一致で数えない語（時間表現・どの記憶にも出る語）
_LEXICAL_STOP: Final[frozenset[str]] = frozenset(
    {"今日", "明日", "昨日", "来週", "今週", "先週", "今度", "週末", "最近", "ユーザー", "話", "感じ"}
)


def content_terms(text: str) -> frozenset[str]:
    return frozenset(t for t in _CONTENT_RUN.findall(text) if t not in _LEXICAL_STOP)


def lexical_overlap(query: str, content: str) -> float:
    """問い合わせの内容語のうち、記憶の本文に含まれる割合（0〜1）。"""
    terms = content_terms(query)
    if not terms:
        return 0.0
    return sum(1 for t in terms if t in content) / len(terms)


@dataclass(frozen=True, slots=True)
class Candidate:
    id: UUID
    kind: str
    content: str
    importance: float
    tags: tuple[str, ...]
    created_at: datetime
    last_referenced_at: datetime | None
    is_user_edited: bool
    similarity: float | None
    lexical: float = 0.0


def recency(
    *,
    created_at: datetime,
    last_referenced_at: datetime | None,
    now: datetime,
    half_life_days: float,
) -> float:
    """0〜1。半減期が 0 以下なら常に 1（減衰させない）。未来の日時（時計の巻き戻し）は 1 とみなす。"""
    if half_life_days <= 0:
        return 1.0
    anchor = max(created_at, last_referenced_at) if last_referenced_at is not None else created_at
    age_days = max((now - anchor).total_seconds() / 86400.0, 0.0)
    return float(0.5 ** (age_days / half_life_days))


def score(
    candidate: Candidate,
    *,
    now: datetime,
    weights: RankingWeights,
    half_life_days: Mapping[str, float],
    kind_boost: Mapping[str, float],
) -> float:
    similarity = min(max(candidate.similarity or 0.0, 0.0), 1.0)
    rec = recency(
        created_at=candidate.created_at,
        last_referenced_at=candidate.last_referenced_at,
        now=now,
        half_life_days=half_life_days.get(candidate.kind, 30.0),
    )
    total = (
        weights.similarity * similarity
        + weights.importance * min(max(candidate.importance, 0.0), 1.0)
        + weights.recency * rec
        + weights.lexical * min(max(candidate.lexical, 0.0), 1.0)
        + kind_boost.get(candidate.kind, 0.0)
    )
    return round(total, 4)


@dataclass(frozen=True, slots=True)
class Ranked:
    candidate: Candidate
    score: float
    pinned: bool = False


def rank(
    candidates: Iterable[Candidate],
    *,
    pinned: Sequence[Candidate],
    now: datetime,
    config: MemoryConfig,
    use_similarity: bool = True,
    exclude_ids: frozenset[UUID] = frozenset(),
    max_items: int | None = None,
    max_chars: int | None = None,
) -> list[Ranked]:
    """常に入れる記憶（pinned）→ 総合点の順。件数・文字数の上限内に収める。

    use_similarity=False（検索用の埋め込みに失敗した場合）は、類似度の下限を使わず重要度と新しさだけで並べる。
    max_items / max_chars は config の上限の代わりに使う（要約の枠を取り置く場合など）。
    """
    limit = config.max_memories if max_items is None else max_items
    budget = config.memory_chars_budget if max_chars is None else max_chars
    selected: list[Ranked] = []
    seen: set[UUID] = set(exclude_ids)
    used_chars = 0
    for item in pinned:
        if item.id in seen or len(selected) >= limit:
            continue
        if selected and used_chars + len(item.content) > budget:
            continue
        seen.add(item.id)
        used_chars += len(item.content)
        selected.append(Ranked(item, PINNED_SCORE, pinned=True))
    pool_all = list(candidates)
    scored = [
        Ranked(
            c,
            score(
                c,
                now=now,
                weights=config.weights,
                half_life_days=config.half_life_days,
                kind_boost=config.kind_boost,
            ),
        )
        for c in pool_all
        if c.id not in seen
        and (not use_similarity or (c.similarity or 0.0) >= config.min_similarity or c.lexical > 0.0)
    ]
    if use_similarity and config.fill_below_threshold:
        # 関係の深い記憶で枠が埋まらなければ、類似度の下限に届かない記憶も総合点（重要度・新しさ）の順に入れる
        # （記憶が少ないうちは全部をキャラに見せる。想起率を優先し、枠と文字数の上限で雑音を抑える）
        seen_scored = {r.candidate.id for r in scored}
        fillers = [
            Ranked(
                c,
                score(
                    c,
                    now=now,
                    weights=config.weights,
                    half_life_days=config.half_life_days,
                    kind_boost=config.kind_boost,
                ),
            )
            for c in pool_all
            if c.id not in seen and c.id not in seen_scored
        ]
        fillers.sort(key=lambda r: (-r.score, -r.candidate.created_at.timestamp()))
    else:
        fillers = []
    # 同点なら類似度 → 新しい順
    scored.sort(key=lambda r: (-r.score, -(r.candidate.similarity or 0.0), -r.candidate.created_at.timestamp()))
    for ranked in [*scored, *fillers]:
        if len(selected) >= limit:
            break
        if ranked.candidate.id in seen:
            continue
        if used_chars + len(ranked.candidate.content) > budget:
            continue
        seen.add(ranked.candidate.id)
        used_chars += len(ranked.candidate.content)
        selected.append(ranked)
    return selected
