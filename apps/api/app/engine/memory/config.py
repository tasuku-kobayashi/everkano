"""メモリエンジンの設定（値の根拠は最終報告・ADR にまとめる）。

`MemoryConfig.from_settings(settings)` で既存の環境変数（MEMORY_* / LLM_MODEL_ANALYSIS / AUDIT_LOG_PROMPTS）から
組み立てる。環境変数の無い調整値（ランキングの重み・半減期など）はコードの定数（評価ハーネスで調整する）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Final

from app.core.config import Settings

# 記憶の種類ごとの「新しさ」の半減期（日）。安定した情報（事実・好み・呼び方）はほとんど減衰させず、
# その場の気持ち（emotion）は 1 週間で半分にする。記憶そのものは消さない（ランキング上の減衰だけ）。
DEFAULT_HALF_LIFE_DAYS: Final[Mapping[str, float]] = MappingProxyType(
    {
        "fact": 180.0,
        "preference": 120.0,
        "relationship": 365.0,
        "episode": 30.0,
        "promise": 30.0,
        "emotion": 7.0,
        "summary": 60.0,
    }
)
# 種類ごとの加点（関係性・約束は会話の一貫性に効くので少し優先。要約は長いので控えめ）
DEFAULT_KIND_BOOST: Final[Mapping[str, float]] = MappingProxyType(
    {
        "fact": 0.0,
        "preference": 0.0,
        "relationship": 0.08,
        "episode": 0.0,
        "promise": 0.05,
        "emotion": 0.0,
        "summary": -0.05,
    }
)


@dataclass(frozen=True, slots=True)
class RankingWeights:
    """score = similarity·w_sim + importance·w_imp + recency·w_rec + lexical·w_lex + kind_boost（M5）。"""

    similarity: float = 0.55
    importance: float = 0.2
    recency: float = 0.15
    lexical: float = 0.1


def _default_prompts_dir() -> Path:
    # apps/api/app/engine/memory/config.py → リポジトリ直下の packages/prompts/templates
    return Path(__file__).resolve().parents[5] / "packages" / "prompts" / "templates"


@dataclass(frozen=True, slots=True)
class MemoryConfig:
    prompts_dir: Path = field(default_factory=_default_prompts_dir)

    # --- 抽出・統合（M1〜M4） -------------------------------------------------
    importance_threshold: float = 0.6  # これ未満の add は保存しない（MEMORY_IMPORTANCE_THRESHOLD）
    dedup_similarity: float = 0.92  # 既存の記憶とこれ以上似ていれば統合（MEMORY_DEDUP_SIMILARITY）
    tombstone_similarity: float = 0.92  # 削除した記憶（墓標）とこれ以上似ていれば作らない（E5）
    promise_dedup_similarity: float = 0.5  # 約束の重複判定（同じ期日で本文の bigram Jaccard がこれ以上）
    max_per_character: int = 500  # ペアあたりの記憶の上限（MEMORY_MAX_PER_CHARACTER, ADR-0024）
    analysis_model: str | None = None  # LLM_MODEL_ANALYSIS（None = LLM_MODEL）
    analysis_max_tokens: int = 1200
    analysis_max_turns: int = 10  # 1 回の分析で扱うターン数（post_turn の ENGINE_POST_TURN_MAX_TURNS と合わせる）
    analysis_related_per_sentence: int = 4  # ユーザーの文ごとに添える似た既存の記憶
    analysis_base_memories: int = 12  # 矛盾の検出用に常に添える事実・関係性・好みの記憶（重要度の高い順）
    analysis_context_memories: int = 24  # 分析に添える既存の記憶の上限
    analysis_context_chars: int = 2400
    skip_trivial_batches: bool = True  # 相づち・あいさつだけのバッチは LLM を呼ばない（E7）
    analysis_pending_promises: int = 10
    max_ops: int = 12
    max_promises: int = 5
    max_character_statements: int = 5
    embedding_timeout_seconds: float = 5.0  # EMBEDDING_TIMEOUT_SECONDS（検索用の埋め込みの打ち切り）

    # --- 検索（M5） --------------------------------------------------------------
    weights: RankingWeights = field(default_factory=RankingWeights)
    half_life_days: Mapping[str, float] = field(default_factory=lambda: DEFAULT_HALF_LIFE_DAYS)
    kind_boost: Mapping[str, float] = field(default_factory=lambda: DEFAULT_KIND_BOOST)
    candidate_pool: int = 50  # 厳密検索で取り出す候補数（この中を総合点で並べ替える）
    min_similarity: float = 0.15  # これ以上の候補を先に入れる（関係の深い記憶）
    fill_below_threshold: bool = True  # 枠が余れば下限未満の記憶も総合点の順に入れる（記憶が少ないうちは全部見せる）
    max_memories: int = 10
    memory_chars_budget: int = 1200
    always_relationship: int = 2  # 呼び方・距離感の記憶は常に入れる
    always_summaries: int = 2  # 最新の要約は常に入れる
    core_facts: int = 3  # 重要度の高い事実（仕事・住まい等）は常に入れる（MemGPT の core memory に相当）
    core_fact_min_importance: float = 0.8
    promise_window: timedelta = timedelta(days=2)  # 期日が ±2 日の約束をプロンプトに入れる（M6）
    max_context_promises: int = 5
    character_memory_limit: int = 6
    character_memory_chars_budget: int = 500
    character_memory_half_life_days: float = 7.0
    shared_event_days: int = 14  # 全ユーザー共通の出来事（予定由来, C9）は直近 14 日分

    # --- 要約（M7） --------------------------------------------------------------
    short_term_message_limit: int = 60  # 短期ウィンドウ（MEMORY_SHORT_TERM_TURNS × 2）
    summary_trigger_message_count: int = 100  # 未要約がこれを超えたら要約（MEMORY_SUMMARY_TRIGGER_TURNS × 2）
    summary_model: str | None = None

    # --- 監査 ---------------------------------------------------------------------
    audit_log_prompts: bool = True  # memory.analysis / memory.summary に入力と生出力を残す（AUDIT_LOG_PROMPTS）

    @classmethod
    def from_settings(cls, settings: Settings, **overrides: object) -> MemoryConfig:
        values: dict[str, object] = {
            "prompts_dir": settings.resolved_prompts_dir,
            "importance_threshold": settings.memory_importance_threshold,
            "dedup_similarity": settings.memory_dedup_similarity,
            "tombstone_similarity": settings.memory_dedup_similarity,
            "max_per_character": settings.memory_max_per_character,
            "analysis_model": settings.llm_model_analysis,
            "summary_model": settings.llm_model_analysis,
            "embedding_timeout_seconds": settings.embedding_timeout_seconds,
            "short_term_message_limit": settings.short_term_message_limit,
            "summary_trigger_message_count": settings.summary_trigger_message_count,
            "audit_log_prompts": settings.audit_log_prompts,
            "analysis_max_turns": settings.engine_post_turn_max_turns,
        }
        values.update(overrides)
        return cls(**values)  # type: ignore[arg-type]
