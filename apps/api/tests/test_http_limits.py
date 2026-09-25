"""外部 HTTP クライアントの接続数・タイムアウトが Fly.io の同時リクエスト上限と整合していること。"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import cast

import httpx

from app.container import build_services
from app.core.db import Pool
from app.core.http import (
    HTTP_MAX_CONNECTIONS,
    HTTP_POOL_TIMEOUT_SECONDS,
    create_jwks_client,
    create_upstream_client,
    upstream_timeout,
)
from app.services.llm import OpenAICompatibleLLM
from app.services.persona import PersonaRepository
from app.services.prompt import PromptBuilder
from tests.conftest import FIXTURES_DIR, PROMPTS_DIR, make_settings

FLY_TOML = Path(__file__).resolve().parents[1] / "fly.toml"
# /chat は応答生成と記憶抽出で同時に 2 本の LLM 接続を使う
LLM_CONNECTIONS_PER_CHAT = 2


def test_connection_pool_covers_fly_hard_limit() -> None:
    config = tomllib.loads(FLY_TOML.read_text(encoding="utf-8"))
    hard_limit = int(config["http_service"]["concurrency"]["hard_limit"])
    assert hard_limit * LLM_CONNECTIONS_PER_CHAT <= HTTP_MAX_CONNECTIONS, (
        "fly.toml の hard_limit を上げたら app/core/http.py の HTTP_MAX_CONNECTIONS も上げること"
    )


def test_pool_wait_is_short_even_with_per_request_timeout() -> None:
    """LLM はリクエスト単位で timeout を渡す。float だと pool 待ちまで 30 秒になるため Timeout を渡すこと。"""
    settings = make_settings(llm_mode="live", llm_api_key="sk-test")
    llm = OpenAICompatibleLLM(settings, httpx.AsyncClient())
    assert isinstance(llm._timeout, httpx.Timeout)
    assert llm._timeout.read == settings.llm_timeout_seconds
    assert llm._timeout.pool == HTTP_POOL_TIMEOUT_SECONDS
    assert upstream_timeout(0.5).pool == 0.5


async def test_jwks_uses_its_own_client() -> None:
    upstream = create_upstream_client(30.0)
    jwks_http = create_jwks_client()
    try:
        services = build_services(
            settings=make_settings(),
            pool=cast(Pool, None),
            http=upstream,
            jwks_http=jwks_http,
            personas=PersonaRepository.load_dir(FIXTURES_DIR / "personas"),
            prompts=PromptBuilder.load_dir(PROMPTS_DIR),
        )
        assert services.jwks._http is jwks_http
        assert services.jwks._http is not services.http
    finally:
        await upstream.aclose()
        await jwks_http.aclose()
