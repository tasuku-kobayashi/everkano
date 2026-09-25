from __future__ import annotations

import pytest
from pydantic import ValidationError

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
