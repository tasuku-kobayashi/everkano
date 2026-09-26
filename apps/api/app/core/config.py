"""環境変数の一元管理と起動時検証（開発依頼書 §15 / BRIEF §2.6）。

- 変数名と既定値はリポジトリ直下の `.env.example`（API セクション）が契約。
- `apps/api/.env` が存在すれば読み込む（環境変数が優先）。
- 空文字の環境変数は「未設定」として扱う（`.env.example` をコピーしただけの状態を許容）。
- 検証に失敗した場合はアプリ起動時に例外となり、プロセスは起動しない。
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Final, Literal, Self
from urllib.parse import parse_qs, urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

API_ROOT = Path(__file__).resolve().parents[2]  # apps/api
ENV_FILE = API_ROOT / ".env"

# DB の memories.embedding は vector(1536) 固定
DB_EMBEDDING_DIMENSIONS = 1536

# コスト推計の既定の価格（DeepSeek V3。円 / 100万トークン。$0.27 / $0.07（キャッシュ）/ $1.10 @ ¥150）
DEFAULT_PRICE_JPY_PER_MTOK: Final[dict[str, float]] = {"input": 40.0, "cached_input": 11.0, "output": 165.0}
DEFAULT_PRICE_MODELS: Final[tuple[str, ...]] = ("deepseek/deepseek-chat", "deepseek-chat", "mock-persona-v1")

# staging / production で既定値（ローカル）のまま起動していないかの検査に使うホスト名
_LOCAL_HOSTS: Final[frozenset[str]] = frozenset(
    {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}  # noqa: S104 - 判定用の定数
)
# staging / production で DATABASE_URL に求める sslmode（libpq 互換。asyncpg の既定 prefer は証明書を検証せず、
# TLS を張れなければ平文に落ちる）。verify-full（Supabase のルート証明書で検証）を推奨する（apps/api/README.md）
_SECURE_SSLMODES: Final[frozenset[str]] = frozenset({"require", "verify-ca", "verify-full"})


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
        # 検証エラーの文面に入力値（LLM_API_KEY・DATABASE_URL のパスワードなど）を含めない（起動失敗のログに残るため）
        hide_input_in_errors=True,
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

    # [追加・任意] ログに記録するクライアントIPを取り出す、プロキシが上書きする信頼済みヘッダー名。
    # Fly.io では "Fly-Client-IP"。未設定時は接続元アドレス（X-Forwarded-For の先頭は偽装可能なため使わない）。
    client_ip_header: str | None = None

    # [追加・任意] リクエスト本文の上限（バイト）。超えたら本文を読まずに 413 を返す（認証より前に効く）。
    # 正規の最大は /chat の 2000 文字（UTF-8 で約 8KB）なので既定 64KiB で十分。
    max_request_body_bytes: int = Field(default=64 * 1024, ge=1024, le=10 * 1024 * 1024)

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
    # [追加・任意] POST /chat 全体（埋め込み + 生成 + 記憶抽出の待ち）の上限秒数。
    # Web の CHAT_TIMEOUT_MS（45秒）より短くし、クライアントが諦めた後に保存されることを防ぐ。
    chat_deadline_seconds: float = Field(default=38.0, gt=0, le=300)
    # [追加・任意] OpenRouter のランキング用ヘッダ（HTTP-Referer / X-Title）
    llm_http_referer: str | None = None
    llm_app_title: str = "everkano"

    # --- Embedding ----------------------------------------------------------
    embedding_mode: Literal["live", "hash"] = "hash"
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: SecretStr | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = DB_EMBEDDING_DIMENSIONS
    # [追加・任意] 埋め込み API の1回あたりのタイムアウトとリトライ回数（LLM とは別。長期記憶の検索は
    # 任意の機能なので、埋め込みが遅いときはチャット全体を待たせずに検索を省略する）
    embedding_timeout_seconds: float = Field(default=5.0, gt=0, le=120)
    embedding_max_retries: int = Field(default=1, ge=0, le=10)

    # --- メモリエンジン（§9） -------------------------------------------------
    memory_short_term_turns: int = Field(default=30, ge=1, le=200)
    memory_summary_trigger_turns: int = Field(default=50, ge=1)
    memory_importance_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    memory_retrieval_top_k: int = Field(default=5, ge=1, le=50)
    memory_dedup_similarity: float = Field(default=0.92, gt=0.0, le=1.0)
    # [追加・任意] ユーザー × キャラあたりの記憶の上限件数。/chat の厳密検索は件数に比例して重くなるため、
    # ユーザーの追加（POST /memories）は上限で 422、自動抽出・要約は重要度の低い自動記憶を入れ替える。
    memory_max_per_character: int = Field(default=500, ge=10, le=100_000)

    # --- レート制限 -------------------------------------------------------------
    rate_limit_chat_per_minute: int = Field(default=20, ge=1)
    rate_limit_comments_per_minute: int = Field(default=10, ge=1)
    # [追加・任意] POST / PATCH /memories（埋め込み API を呼ぶ）の上限
    rate_limit_memories_per_minute: int = Field(default=30, ge=1)

    # --- コメント自動返信 -------------------------------------------------------
    comment_auto_reply_probability: float = Field(default=1.0, ge=0.0, le=1.0)

    # --- 監査ログ ---------------------------------------------------------------
    audit_log_prompts: bool = True

    # --- キャラクターエンジン v1.0（ENGINE_BRIEF §2.3〜2.5・2.11） ------------------------------
    # 用途ごとのモデル（未設定なら LLM_MODEL）。分析系（記憶の抽出・要約・好感度の評価）は安いモデルに分けられる
    llm_model_analysis: str | None = None
    llm_model_proactive: str | None = None
    llm_model_caption: str | None = None
    # MockLLM のストリーミングで、チャンクごとに入れる待ち時間（ミリ秒。E8 のレイテンシ計測・表示確認用）
    llm_mock_stream_delay_ms: int = Field(default=0, ge=0, le=5000)

    # 3つの仕組み + 自発メッセージの有効/無効（評価ハーネスの「素の LLM」ベースライン用。既定はすべて有効）
    engine_memory_enabled: bool = True
    engine_calendar_enabled: bool = True
    engine_affinity_enabled: bool = True
    engine_proactive_enabled: bool = True

    # 非同期ジョブ（Postgres キュー）。API プロセス内で動かすか（本番は worker プロセスグループで動かす）
    engine_worker_enabled: bool = True
    engine_worker_concurrency: int = Field(default=2, ge=1, le=32)
    engine_worker_poll_interval_seconds: float = Field(default=1.0, gt=0, le=60)
    engine_job_max_attempts: int = Field(default=5, ge=1, le=20)
    engine_job_backoff_base_seconds: float = Field(default=30.0, ge=0, le=3600)
    engine_job_backoff_max_seconds: float = Field(default=3600.0, ge=0, le=86400)
    # running のまま止まったジョブ（プロセスの強制終了など）を再実行に戻すまでの秒数
    engine_job_lock_timeout_seconds: float = Field(default=600.0, ge=10, le=86400)
    engine_job_timeout_seconds: float = Field(default=300.0, gt=0, le=3600)
    # 完了・dead のジョブを消すまでの日数（jobs.cleanup）
    engine_job_retention_days: int = Field(default=7, ge=1, le=365)
    # 返答後の非同期処理（記憶の抽出・約束の予定化・好感度の評価）を何秒待ってからまとめて行うか（デバウンス）。
    # 続けて話すあいだは後ろにずらし（最大この 6 倍 = 18 分）、会話が止まってから 1 回だけ分析する（E7 のコスト）。
    # 180 秒の根拠（docs/eval/README.md §7 の実験）: 分析の回数/発言は 20 秒 1.87・120 秒 1.04・180 秒 0.57・
    # 300 秒 0.55（180 秒で下げ止まる）。
    # 180 秒なら「覚えました」の通知・約束の登録は会話が止まって 3 分以内（最大 18 分）で、鮮度を大きく損なわない
    engine_post_turn_delay_seconds: float = Field(default=180.0, ge=0, le=3600)
    engine_post_turn_max_turns: int = Field(default=10, ge=1, le=50)

    # 定期実行（スケジューラ）。API プロセス内で動かすか。リーダー選出は pg_try_advisory_lock
    engine_scheduler_enabled: bool = True
    engine_scheduler_poll_interval_seconds: float = Field(default=30.0, gt=0, le=3600)
    engine_calendar_ensure_interval_seconds: int = Field(default=3600, ge=60)
    engine_calendar_days_ahead: int = Field(default=7, ge=1, le=60)
    engine_calendar_tick_interval_seconds: int = Field(default=300, ge=30)
    engine_proactive_scan_interval_seconds: int = Field(default=600, ge=60)
    # 好感度の日次処理（気まずさ・不満の減衰など）を行う時刻（JST の時）
    engine_affinity_daily_hour_jst: int = Field(default=4, ge=0, le=23)
    # 定期実行の記録（engine_schedules）とリーダーのロックの名前空間。評価ハーネス・テストが共有の DB で
    # 時計を早送りしても、本番・開発サーバーの記録と混ざらないようにする（通常は空）
    engine_schedule_namespace: str = Field(default="", max_length=40, pattern=r"^[a-z0-9_.:-]*$")

    # 自発メッセージ（E4）: 1ユーザー1日あたりの上限（全キャラ合計）と、既定の送らない時間帯（JST の時）
    engine_proactive_daily_limit: int = Field(default=3, ge=0, le=20)
    engine_proactive_quiet_start: int = Field(default=0, ge=0, le=23)
    engine_proactive_quiet_end: int = Field(default=7, ge=0, le=23)

    # Context Assembler の全体の締め切り（秒）。超えた要素は省略して返答を続ける（E8）
    engine_context_timeout_seconds: float = Field(default=1.5, gt=0, le=30)
    # POST /chat/stream の keep-alive コメントの間隔（秒）
    chat_stream_heartbeat_seconds: float = Field(default=10.0, gt=0, le=60)

    # E6 の相談窓口と返答文面（YAML）。未設定なら packages/prompts/safety/resources.ja.yaml
    safety_resources_path: Path | None = None

    # コスト推計用の価格表（JSON。モデル名 → 100万トークンあたりの円: input / cached_input / output）。
    # 未設定なら DeepSeek V3 の既定値（ENGINE_BRIEF §2.11）
    engine_price_table_json: str | None = None

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
        if self.app_env in ("staging", "production"):
            errors.extend(self._deployed_env_errors())
        if self.engine_job_backoff_base_seconds > self.engine_job_backoff_max_seconds:
            errors.append("ENGINE_JOB_BACKOFF_BASE_SECONDS は ENGINE_JOB_BACKOFF_MAX_SECONDS 以下にしてください")
        try:
            _ = self.price_table
        except ValueError as exc:
            errors.append(f"ENGINE_PRICE_TABLE_JSON が不正です: {exc}")
        if errors:
            raise ValueError("; ".join(errors))

        if self.personas_dir is None:
            self.personas_dir = _find_repo_dir("packages", "personas")
        if self.prompts_dir is None:
            self.prompts_dir = _find_repo_dir("packages", "prompts", "templates")
        elif (self.prompts_dir / "templates").is_dir():
            # packages/prompts を指定された場合も templates/ を解決する
            self.prompts_dir = self.prompts_dir / "templates"
        if self.safety_resources_path is None:
            safety_dir = _find_repo_dir("packages", "prompts", "safety")
            if safety_dir is not None:
                self.safety_resources_path = safety_dir / "resources.ja.yaml"
        if self.safety_resources_path is None or not self.safety_resources_path.is_file():
            raise ValueError(f"SAFETY_RESOURCES_PATH が見つかりません: {self.safety_resources_path}")
        if self.personas_dir is None or not self.personas_dir.is_dir():
            raise ValueError(f"PERSONAS_DIR が見つかりません: {self.personas_dir}")
        if self.prompts_dir is None or not self.prompts_dir.is_dir():
            raise ValueError(f"PROMPTS_DIR が見つかりません: {self.prompts_dir}")
        return self

    def _deployed_env_errors(self) -> list[str]:
        """staging / production でローカル既定値のまま起動していないか（Fly secrets の登録漏れ）を検査する。"""
        errors: list[str] = []
        env = self.app_env
        supabase = urlsplit(self.supabase_url)
        if (supabase.hostname or "") in _LOCAL_HOSTS:
            errors.append(
                f"APP_ENV={env} では SUPABASE_URL にローカルのURLを指定できません"
                "（fly secrets set SUPABASE_URL=https://<project-ref>.supabase.co を確認してください）"
            )
        elif supabase.scheme != "https":
            errors.append(f"APP_ENV={env} では SUPABASE_URL は https:// で指定してください")
        errors.extend(self._database_tls_errors())
        remote_origins = [o for o in self.cors_allow_origins if (urlsplit(o).hostname or "") not in _LOCAL_HOSTS]
        if not remote_origins:
            errors.append(
                f"APP_ENV={env} では CORS_ALLOW_ORIGINS に Web のURLを指定してください"
                "（fly secrets set CORS_ALLOW_ORIGINS=https://<web>.vercel.app を確認してください）"
            )
        return errors

    def _database_tls_errors(self) -> list[str]:
        """リモートの DB への接続で TLS を必須にしているか（DATABASE_URL の sslmode か PGSSLMODE）。

        文面に DATABASE_URL（パスワードを含む）は入れない。ループバック・Unix ソケットは対象外。
        """
        url = urlsplit(self.database_url)
        if (url.hostname or "") in _LOCAL_HOSTS or not url.hostname:
            return []
        sslmode = self.database_sslmode
        if sslmode in _SECURE_SSLMODES:
            return []
        return [
            f"APP_ENV={self.app_env} では DATABASE_URL に sslmode=verify-full（または require）を指定してください"
            f"（現在: {sslmode or '未指定 = prefer'}。証明書を検証せず、TLS を張れなければ平文で接続します。"
            "例: postgresql://...?sslmode=verify-full&sslrootcert=/app/certs/supabase-ca.crt。apps/api/README.md 参照）"
        ]

    # -------------------------------------------------------------------------
    @property
    def database_sslmode(self) -> str | None:
        """asyncpg が使う sslmode（DATABASE_URL の sslmode → 環境変数 PGSSLMODE。どちらも無ければ None = prefer）。"""
        modes = parse_qs(urlsplit(self.database_url).query).get("sslmode")
        return modes[-1] if modes else os.environ.get("PGSSLMODE")

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
    def resolved_safety_resources_path(self) -> Path:
        if self.safety_resources_path is None:  # _validate_combination で解決済みのはず
            raise RuntimeError("SAFETY_RESOURCES_PATH is not resolved")
        return self.safety_resources_path

    @property
    def resolved_prompts_dir(self) -> Path:
        if self.prompts_dir is None:  # _validate_combination で解決済みのはず
            raise RuntimeError("PROMPTS_DIR is not resolved")
        return self.prompts_dir

    def llm_model_for(self, purpose: str) -> str:
        """用途ごとのモデル名（LLM_MODEL_ANALYSIS などが未設定なら LLM_MODEL）。"""
        override = {
            "memory_analysis": self.llm_model_analysis,
            "memory_summary": self.llm_model_analysis,
            "affinity_eval": self.llm_model_analysis,
            "proactive_message": self.llm_model_proactive,
            "feed_caption": self.llm_model_caption,
        }.get(purpose)
        return override or self.llm_model

    @property
    def purpose_models(self) -> dict[str, str]:
        """既定（LLM_MODEL）と異なるモデルを使う用途だけの対応表。"""
        purposes: tuple[str, ...] = (
            "memory_analysis",
            "memory_summary",
            "affinity_eval",
            "proactive_message",
            "feed_caption",
        )
        return {p: m for p in purposes if (m := self.llm_model_for(p)) != self.llm_model}

    @property
    def price_table(self) -> dict[str, dict[str, float]]:
        """コスト推計用の価格表（モデル名 → {input, cached_input, output}: 100万トークンあたりの円）。"""
        if self.engine_price_table_json is None:
            return {name: dict(DEFAULT_PRICE_JPY_PER_MTOK) for name in DEFAULT_PRICE_MODELS}
        raw = json.loads(self.engine_price_table_json)
        if not isinstance(raw, dict) or not raw:
            raise ValueError("モデル名をキーにしたオブジェクトを指定してください")
        table: dict[str, dict[str, float]] = {}
        for model, prices in raw.items():
            if not isinstance(prices, dict) or not {"input", "output"} <= set(prices):
                raise ValueError(f"{model}: input と output（円 / 100万トークン）が必要です")
            entry: dict[str, float] = {}
            for key in ("input", "cached_input", "output"):
                value = prices.get(key, prices["input"] if key == "cached_input" else None)
                if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
                    raise ValueError(f"{model}.{key} は 0 以上の数値にしてください")
                entry[key] = float(value)
            table[str(model)] = entry
        return table

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
