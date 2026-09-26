from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from tests.conftest import REPO_ROOT, make_settings


def test_defaults_match_env_example() -> None:
    s = make_settings()
    assert s.memory_short_term_turns == 30
    assert s.short_term_message_limit == 60
    assert s.summary_trigger_message_count == 100
    assert s.memory_importance_threshold == 0.6
    assert s.memory_retrieval_top_k == 5
    assert s.memory_dedup_similarity == 0.92
    assert s.embedding_dimensions == 1536
    assert s.jwks_url == "http://127.0.0.1:54321/auth/v1/.well-known/jwks.json"
    assert s.jwt_issuer == "http://127.0.0.1:54321/auth/v1"


def test_production_forbids_mock_llm() -> None:
    with pytest.raises(ValidationError, match="LLM_MODE=mock"):
        make_settings(app_env="production", llm_mode="mock")


def test_live_llm_requires_key() -> None:
    with pytest.raises(ValidationError, match="LLM_API_KEY"):
        make_settings(llm_mode="live", llm_api_key=None)
    s = make_settings(llm_mode="live", llm_api_key="sk-test")
    assert s.llm_api_key is not None


def test_live_embedding_requires_key() -> None:
    with pytest.raises(ValidationError, match="EMBEDDING_API_KEY"):
        make_settings(embedding_mode="live")


DEPLOYED = {
    "supabase_url": "https://abcdefgh.supabase.co",
    "cors_allow_origins": ["https://everkano.vercel.app"],
}


def test_production_with_live_llm_is_valid() -> None:
    s = make_settings(app_env="production", llm_mode="live", llm_api_key="sk-test", **DEPLOYED)
    assert s.app_env == "production"
    assert s.jwt_issuer == "https://abcdefgh.supabase.co/auth/v1"


@pytest.mark.parametrize("app_env", ["staging", "production"])
def test_deployed_env_rejects_local_defaults(app_env: str) -> None:
    live = {"llm_mode": "live", "llm_api_key": "sk-test"}
    # SUPABASE_URL の登録漏れ（ローカル既定値のまま）
    with pytest.raises(ValidationError, match="SUPABASE_URL"):
        make_settings(app_env=app_env, **live, cors_allow_origins=DEPLOYED["cors_allow_origins"])
    with pytest.raises(ValidationError, match="SUPABASE_URL"):
        make_settings(
            app_env=app_env,
            **live,
            supabase_url="http://host.docker.internal:54321",
            cors_allow_origins=DEPLOYED["cors_allow_origins"],
        )
    with pytest.raises(ValidationError, match="https://"):
        make_settings(
            app_env=app_env,
            **live,
            supabase_url="http://supabase.example.com",
            cors_allow_origins=DEPLOYED["cors_allow_origins"],
        )
    # CORS_ALLOW_ORIGINS の登録漏れ（localhost のみ / 空）
    with pytest.raises(ValidationError, match="CORS_ALLOW_ORIGINS"):
        make_settings(app_env=app_env, **live, supabase_url=DEPLOYED["supabase_url"])
    with pytest.raises(ValidationError, match="CORS_ALLOW_ORIGINS"):
        make_settings(app_env=app_env, **live, supabase_url=DEPLOYED["supabase_url"], cors_allow_origins=[])
    ok = make_settings(
        app_env=app_env,
        **live,
        supabase_url=DEPLOYED["supabase_url"],
        cors_allow_origins=["http://localhost:3000", "https://everkano.vercel.app"],
    )
    assert ok.app_env == app_env


def test_local_accepts_local_defaults() -> None:
    s = make_settings(app_env="local")
    assert s.supabase_url == "http://127.0.0.1:54321"
    assert s.chat_deadline_seconds == 38.0


def test_embedding_dimensions_must_match_db() -> None:
    with pytest.raises(ValidationError, match="EMBEDDING_DIMENSIONS"):
        make_settings(embedding_dimensions=768)


def test_cors_origins_are_split(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://a.example, https://b.example/")
    s = make_settings()
    assert s.cors_allow_origins == ["https://a.example", "https://b.example"]


def test_empty_env_values_are_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("SENTRY_DSN", "")
    s = make_settings()
    assert s.llm_api_key is None
    assert s.sentry_dsn is None


def test_invalid_ranges_are_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(memory_importance_threshold=1.5)
    with pytest.raises(ValidationError):
        make_settings(comment_auto_reply_probability=-0.1)
    with pytest.raises(ValidationError, match="DATABASE_POOL_MIN_SIZE"):
        make_settings(database_pool_min_size=5, database_pool_max_size=2)
    with pytest.raises(ValidationError, match="postgresql://"):
        make_settings(database_url="mysql://x")


def test_prompts_dir_accepts_package_root() -> None:
    s = make_settings(prompts_dir=REPO_ROOT / "packages" / "prompts")
    assert s.resolved_prompts_dir.name == "templates"


def test_missing_personas_dir_is_rejected() -> None:
    with pytest.raises(ValidationError, match="PERSONAS_DIR"):
        make_settings(personas_dir="/nonexistent/personas")


def test_new_safety_limits_have_safe_defaults() -> None:
    s = make_settings()
    assert s.max_request_body_bytes == 64 * 1024
    assert s.embedding_timeout_seconds == 5.0
    assert s.embedding_max_retries == 1
    # 検索用の埋め込みの打ち切りは /chat の締め切りより十分短い
    assert s.embedding_timeout_seconds < s.chat_deadline_seconds / 2
    assert s.memory_max_per_character == 500
    assert s.rate_limit_memories_per_minute == 30
    with pytest.raises(ValidationError):
        make_settings(max_request_body_bytes=100)
    with pytest.raises(ValidationError):
        make_settings(memory_max_per_character=0)


# 実在しないダミーの接続文字列（パスワードがエラー文面に出ないことの検査用）
REMOTE_DB = (
    "postgresql://postgres.abcdefgh:db-PASSWORD-sentinel@"  # check-secrets: allow（ダミー）
    "aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres"
)
BAD_SCHEME_DB = "mysql://root:db-PASSWORD-sentinel@db.example.com/app"  # check-secrets: allow（ダミー）
SECRET_SENTINEL = "sk-or-SECRET-sentinel-123"  # check-secrets: allow（ダミー）


@pytest.mark.parametrize("app_env", ["staging", "production"])
def test_deployed_env_requires_tls_to_a_remote_database(app_env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """asyncpg の既定（prefer）は証明書を検証せず、TLS を張れなければ平文に落ちる → リモート DB では拒否する。"""
    monkeypatch.delenv("PGSSLMODE", raising=False)
    live = {"llm_mode": "live", "llm_api_key": "sk-test", **DEPLOYED}
    for url in (REMOTE_DB, f"{REMOTE_DB}?sslmode=prefer", f"{REMOTE_DB}?sslmode=disable", f"{REMOTE_DB}?sslmode=allow"):
        with pytest.raises(ValidationError, match="sslmode") as exc:
            make_settings(app_env=app_env, database_url=url, **live)
        # エラーの文面に DB のパスワードを出さない
        assert "db-PASSWORD-sentinel" not in str(exc.value)
    for mode in ("require", "verify-ca", "verify-full"):
        ok = make_settings(app_env=app_env, database_url=f"{REMOTE_DB}?sslmode={mode}&application_name=x", **live)
        assert ok.database_url.endswith("application_name=x")
        assert ok.database_sslmode == mode  # 起動ログに出す値
    # 後に書いた sslmode が有効（asyncpg と同じ解釈）
    with pytest.raises(ValidationError, match="sslmode"):
        make_settings(app_env=app_env, database_url=f"{REMOTE_DB}?sslmode=require&sslmode=prefer", **live)
    # 環境変数 PGSSLMODE でもよい（asyncpg は DSN に無ければこれを使う）
    monkeypatch.setenv("PGSSLMODE", "verify-full")
    assert make_settings(app_env=app_env, database_url=REMOTE_DB, **live).app_env == app_env
    # ループバック（サイドカーのプロキシ等）とローカル環境は対象外
    monkeypatch.delenv("PGSSLMODE")
    assert make_settings(app_env=app_env, **live).database_url.startswith("postgresql://postgres:postgres@127.0.0.1")
    assert make_settings(app_env="local", database_url=REMOTE_DB).app_env == "local"


def test_validation_errors_do_not_echo_secret_inputs() -> None:
    """起動失敗のログ（Fly のログ・ログ転送先）に API キーや DB のパスワードが平文で出ない。

    pydantic は既定で入力値（設定全体の dict）を末尾を残して省略表示するため、最後の項目の秘密値が残っていた。
    """
    for last in ("llm_api_key", "sentry_dsn"):
        with pytest.raises(ValidationError) as exc:
            # staging で SUPABASE_URL / CORS が未設定 → モデル全体の検証エラー（秘密値を最後の項目にする）
            make_settings(app_env="staging", llm_mode="live", **{last: SECRET_SENTINEL})
        message = str(exc.value)
        assert "SUPABASE_URL" in message
        assert "sentinel" not in message
        assert "input_value" not in message
    # 項目単位の検証エラー（URL の形式違い）でも入力値を出さない
    with pytest.raises(ValidationError) as exc:
        make_settings(database_url=BAD_SCHEME_DB)
    assert "PASSWORD" not in str(exc.value)


# ---------------------------------------------------------------------------
# キャラクターエンジン v1.0 の設定
# ---------------------------------------------------------------------------


def test_engine_defaults() -> None:
    s = Settings(_env_file=None)  # make_settings はテスト用にワーカー等を止めるので素の既定値で確かめる
    assert (s.engine_memory_enabled, s.engine_calendar_enabled, s.engine_affinity_enabled) == (True, True, True)
    assert s.engine_proactive_enabled is True
    assert s.engine_worker_enabled is True
    assert s.engine_scheduler_enabled is True
    assert s.engine_post_turn_delay_seconds == 180
    assert s.engine_post_turn_max_turns == 10
    assert s.engine_job_max_attempts == 5
    assert s.engine_calendar_days_ahead == 7
    assert s.engine_affinity_daily_hour_jst == 4
    assert s.engine_proactive_daily_limit == 3
    assert (s.engine_proactive_quiet_start, s.engine_proactive_quiet_end) == (0, 7)
    assert s.llm_mock_stream_delay_ms == 0
    assert s.safety_resources_path == REPO_ROOT / "packages" / "prompts" / "safety" / "resources.ja.yaml"
    assert s.price_table["deepseek/deepseek-chat"] == {"input": 40.0, "cached_input": 11.0, "output": 165.0}


def test_purpose_models_fall_back_to_llm_model() -> None:
    s = make_settings(llm_model="base")
    assert s.llm_model_for("chat") == "base"
    assert s.llm_model_for("affinity_eval") == "base"
    assert s.purpose_models == {}
    s = make_settings(llm_model="base", llm_model_analysis="cheap", llm_model_caption="caption")
    assert s.llm_model_for("memory_analysis") == "cheap"
    assert s.llm_model_for("memory_summary") == "cheap"
    assert s.llm_model_for("affinity_eval") == "cheap"
    assert s.llm_model_for("feed_caption") == "caption"
    assert s.llm_model_for("proactive_message") == "base"
    assert s.llm_model_for("chat") == "base"


def test_price_table_json_is_validated() -> None:
    s = make_settings(engine_price_table_json='{"m": {"input": 1, "output": 2}}')
    assert s.price_table == {"m": {"input": 1.0, "cached_input": 1.0, "output": 2.0}}
    for bad in ("not json", "[]", "{}", '{"m": {"input": 1}}', '{"m": {"input": -1, "output": 1}}'):
        with pytest.raises(ValidationError, match="ENGINE_PRICE_TABLE_JSON"):
            make_settings(engine_price_table_json=bad)


def test_engine_ranges_are_validated() -> None:
    with pytest.raises(ValidationError, match="ENGINE_JOB_BACKOFF_BASE_SECONDS"):
        make_settings(engine_job_backoff_base_seconds=100, engine_job_backoff_max_seconds=10)
    with pytest.raises(ValidationError):
        make_settings(engine_affinity_daily_hour_jst=24)
    with pytest.raises(ValidationError):
        make_settings(llm_mock_stream_delay_ms=-1)
    with pytest.raises(ValidationError, match="SAFETY_RESOURCES_PATH"):
        make_settings(safety_resources_path=REPO_ROOT / "missing.yaml")


def test_engine_flags_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENGINE_MEMORY_ENABLED", "false")
    monkeypatch.setenv("ENGINE_WORKER_ENABLED", "0")
    monkeypatch.setenv("LLM_MODEL_ANALYSIS", "deepseek/deepseek-chat")
    s = Settings(_env_file=None)
    assert s.engine_memory_enabled is False
    assert s.engine_worker_enabled is False
    assert s.llm_model_analysis == "deepseek/deepseek-chat"
