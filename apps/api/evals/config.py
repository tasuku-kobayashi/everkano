"""評価の実行設定（CLI の引数）と、アプリの Settings の組み立て。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal

from app.core.config import Settings
from evals.prompts import REPO_ROOT
from evals.timeline import DEFAULT_START

Mode = Literal["engine", "baseline"]
LLMMode = Literal["mock", "live"]

DEFAULT_DATABASE_URL: Final[str] = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
RESULTS_DIR: Final[Path] = REPO_ROOT / "docs" / "eval" / "results"
HISTORY_PATH: Final[Path] = REPO_ROOT / "docs" / "eval" / "history.md"
ENGINE_FLAGS: Final[tuple[str, ...]] = (
    "engine_memory_enabled",
    "engine_calendar_enabled",
    "engine_affinity_enabled",
    "engine_proactive_enabled",
)


@dataclass(frozen=True, slots=True)
class RunConfig:
    days: int = 30
    scenarios: tuple[str, ...] = ()
    modes: tuple[Mode, ...] = ("baseline", "engine")
    llm: LLMMode = "mock"
    judge: Literal["rule", "llm"] = "rule"
    sim: Literal["template", "llm"] = "template"
    seed: int = 7
    start: datetime = DEFAULT_START
    step: timedelta = timedelta(minutes=10)
    max_cost_jpy: float = 3000.0
    keep: bool = False
    label: str = ""
    out_dir: Path = RESULTS_DIR
    history_path: Path | None = HISTORY_PATH
    database_url: str = DEFAULT_DATABASE_URL
    all_characters: bool = True  # 10 体すべて（予定の一貫性は全キャラが対象）。False = シナリオのキャラだけ
    min_fact_age_days: int = 7
    tokens_per_char: float = 1.0
    # mock の費用見積もりを本番のモデルのトークナイザ（Hugging Face の tokenizer.json）で数える（evals/tokenizer.py）
    tokenizer: Path | None = None
    price_model: str | None = None  # mock の費用見積もりに使うモデル（None = LLM_MODEL）
    judge_model: str | None = None
    sim_model: str | None = None
    model_ttft_ms: tuple[float, ...] = (1000.0, 1500.0, 3000.0)  # E8 の推計に使うモデルの TTFT の仮定（楽観/中央/悲観）
    # E8 の推計: mock の計測に含まれない本番の待ち（ms）。埋め込み API（記憶の検索の前。エンジンのみ）と DB までの往復
    embedding_api_ms: float = 200.0
    db_network_ms: float = 20.0
    run_e1: bool = True
    verbose: bool = False
    write_results: bool = True
    settings_overrides: dict[str, Any] = field(default_factory=dict)

    @property
    def is_live(self) -> bool:
        return self.llm == "live"


def build_settings(config: RunConfig, mode: Mode, namespace: str) -> Settings:
    """評価用の Settings。worker・スケジューラの常駐はしない（ハーネスが時計を進めて動かす）。

    live の API キーなどは環境変数 / apps/api/.env から読む（LLM_API_KEY・LLM_BASE_URL・LLM_MODEL など）。
    """
    values: dict[str, Any] = {
        "app_env": "local",
        "log_level": "DEBUG" if config.verbose else "WARNING",
        "database_url": config.database_url,
        "database_pool_min_size": 1,
        "database_pool_max_size": 8,
        "llm_mode": config.llm,
        "rate_limit_chat_per_minute": 100_000,
        "engine_worker_enabled": False,
        "engine_scheduler_enabled": False,
        "engine_schedule_namespace": namespace,
        "llm_mock_stream_delay_ms": 0,
    }
    if config.llm == "mock":
        values["embedding_mode"] = "hash"  # live は環境変数 / .env の EMBEDDING_MODE に従う（既定 hash）
    enabled = mode == "engine"
    for flag in ENGINE_FLAGS:
        values[flag] = enabled
    values.update(config.settings_overrides)
    return Settings(**values)
