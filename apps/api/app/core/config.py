"""環境変数の一元管理と起動時検証（開発依頼書 §15 / BRIEF §2.6）。

- 変数名と既定値はリポジトリ直下の `.env.example`（API セクション）が契約。
- `apps/api/.env` が存在すれば読み込む（環境変数が優先）。
- 空文字の環境変数は「未設定」として扱う（`.env.example` をコピーしただけの状態を許容）。
- 検証に失敗した場合はアプリ起動時に例外となり、プロセスは起動しない。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

API_ROOT = Path(__file__).resolve().parents[2]  # apps/api
ENV_FILE = API_ROOT / ".env"

# DB の memories.embedding は vector(1536) 固定
DB_EMBEDDING_DIMENSIONS = 1536


def _find_repo_dir(*parts: str) -> Path | None:
    """このファイルから上位ディレクトリを辿り `<ancestor>/<parts...>` が存在する最初のパスを返す。"""
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        candidate = ancestor.joinpath(*parts)
        if candidate.is_dir():
            return candidate
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
    )

    # --- アプリ ---------------------------------------------------------------
    app_env: Literal["local", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # --- DB -----------------------------------------------------------------
    database_url: str = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
    database_statement_cache_size: int = Field(default=100, ge=0)
    database_pool_min_size: int = Field(default=1, ge=0)
    database_pool_max_size: int = Field(default=10, ge=1)

    # --- Supabase Auth ------------------------------------------------------
    supabase_url: str = "http://127.0.0.1:54321"
    supabase_jwt_secret: SecretStr | None = None
    supabase_jwt_audience: str = "authenticated"
    # [追加・任意] JWT の iss 期待値。未設定時は `{SUPABASE_URL}/auth/v1`。
    # Docker 内から host.docker.internal 経由で Supabase を参照する場合など、
    # SUPABASE_URL とトークン発行元のURLが異なるときに設定する。
    supabase_jwt_issuer: str | None = None
    jwks_cache_ttl_seconds: int = Field(default=600, ge=10)

    # --- CORS ---------------------------------------------------------------
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    # --- LLM ----------------------------------------------------------------
    llm_mode: Literal["live", "mock"] = "mock"
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: SecretStr | None = None
    llm_model: str = "deepseek/deepseek-chat"
    llm_temperature: float = Field(default=0.8, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=400, ge=16, le=8192)
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=10)
    # [追加・任意] OpenRouter のランキング用ヘッダ（HTTP-Referer / X-Title）
    llm_http_referer: str | None = None
    llm_app_title: str = "everkano"

    # --- Embedding ----------------------------------------------------------
    embedding_mode: Literal["live", "hash"] = "hash"
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: SecretStr | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = DB_EMBEDDING_DIMENSIONS

    # --- メモリエンジン（§9） -------------------------------------------------
    memory_short_term_turns: int = Field(default=30, ge=1, le=200)
    memory_summary_trigger_turns: int = Field(default=50, ge=1)
    memory_importance_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    memory_retrieval_top_k: int = Field(default=5, ge=1, le=50)
    memory_dedup_similarity: float = Field(default=0.92, gt=0.0, le=1.0)

    # --- レート制限 -------------------------------------------------------------
    rate_limit_chat_per_minute: int = Field(default=20, ge=1)
    rate_limit_comments_per_minute: int = Field(default=10, ge=1)

    # --- コメント自動返信 -------------------------------------------------------
    comment_auto_reply_probability: float = Field(default=1.0, ge=0.0, le=1.0)

    # --- 監査ログ ---------------------------------------------------------------
    audit_log_prompts: bool = True

    # --- ペルソナ / プロンプト ----------------------------------------------------
    personas_dir: Path | None = None
    prompts_dir: Path | None = None

    # --- 監視 -----------------------------------------------------------------
    sentry_dsn: str | None = None

    # -------------------------------------------------------------------------
    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [o.strip().rstrip("/") for o in value.split(",") if o.strip()]
        return value

    @field_validator("supabase_url", "llm_base_url", "embedding_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("http:// または https:// で始まるURLを指定してください")
        return value

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, value: str) -> str:
        if not value.startswith(("postgresql://", "postgres://")):
            raise ValueError("DATABASE_URL は postgresql:// で始まる接続文字列を指定してください")
        return value

    @model_validator(mode="after")
    def _validate_combination(self) -> Self:
        errors: list[str] = []
        if self.app_env == "production" and self.llm_mode == "mock":
            errors.append("APP_ENV=production では LLM_MODE=mock は使用できません")
        if self.llm_mode == "live" and not _has_secret(self.llm_api_key):
            errors.append("LLM_MODE=live には LLM_API_KEY が必要です")
        if self.embedding_mode == "live" and not _has_secret(self.embedding_api_key):
            errors.append("EMBEDDING_MODE=live には EMBEDDING_API_KEY が必要です")
        if self.embedding_dimensions != DB_EMBEDDING_DIMENSIONS:
            errors.append(f"EMBEDDING_DIMENSIONS は DB の vector({DB_EMBEDDING_DIMENSIONS}) と一致させてください")
        if self.database_pool_min_size > self.database_pool_max_size:
            errors.append("DATABASE_POOL_MIN_SIZE は DATABASE_POOL_MAX_SIZE 以下にしてください")
        if self.app_env == "production" and "*" in self.cors_allow_origins:
            errors.append("APP_ENV=production では CORS_ALLOW_ORIGINS に * を指定できません")
        if errors:
            raise ValueError("; ".join(errors))

        if self.personas_dir is None:
            self.personas_dir = _find_repo_dir("packages", "personas")
        if self.prompts_dir is None:
            self.prompts_dir = _find_repo_dir("packages", "prompts", "templates")
        elif (self.prompts_dir / "templates").is_dir():
            # packages/prompts を指定された場合も templates/ を解決する
            self.prompts_dir = self.prompts_dir / "templates"
        if self.personas_dir is None or not self.personas_dir.is_dir():
            raise ValueError(f"PERSONAS_DIR が見つかりません: {self.personas_dir}")
        if self.prompts_dir is None or not self.prompts_dir.is_dir():
            raise ValueError(f"PROMPTS_DIR が見つかりません: {self.prompts_dir}")
        return self

    # -------------------------------------------------------------------------
    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_url}/auth/v1/.well-known/jwks.json"

    @property
    def jwt_issuer(self) -> str:
        return (self.supabase_jwt_issuer or f"{self.supabase_url}/auth/v1").rstrip("/")

    @property
    def resolved_personas_dir(self) -> Path:
        if self.personas_dir is None:  # _validate_combination で解決済みのはず
            raise RuntimeError("PERSONAS_DIR is not resolved")
        return self.personas_dir

    @property
    def resolved_prompts_dir(self) -> Path:
        if self.prompts_dir is None:  # _validate_combination で解決済みのはず
            raise RuntimeError("PROMPTS_DIR is not resolved")
        return self.prompts_dir

    @property
    def short_term_message_limit(self) -> int:
        """短期メモリで取得するメッセージ数（1ターン = ユーザー + キャラの2件）。"""
        return self.memory_short_term_turns * 2

    @property
    def summary_trigger_message_count(self) -> int:
        """未要約メッセージがこの件数を超えたら中期要約を行う。"""
        return self.memory_summary_trigger_turns * 2


def _has_secret(value: SecretStr | None) -> bool:
    return value is not None and value.get_secret_value().strip() != ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
