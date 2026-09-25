"""OpenAI 互換 `/embeddings` クライアント（EMBEDDING_MODE=live = 本番の既定）を httpx.MockTransport で検証する。

リトライ（429 / 5xx / 通信エラー）・再試行しない 4xx・応答の形の検査は本番でしか通らない経路のため、ここで確認する。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.services.embedding import EmbeddingError, OpenAICompatibleEmbedding, create_embedding_client
from tests.conftest import make_settings

DIM = 1536
Handler = Callable[[httpx.Request, int], httpx.Response]


def _vector(seed: float) -> list[float]:
    return [seed] * DIM


def _ok(request: httpx.Request, _attempt: int) -> httpx.Response:
    body = json.loads(request.content)
    # 順序を入れ替えて返す（index で並べ直すこと）
    data = [{"index": i, "embedding": _vector(float(i + 1))} for i in range(len(body["input"]))]
    return httpx.Response(200, json={"data": list(reversed(data)), "model": body["model"]})


def _client(handler: Handler, **settings: Any) -> tuple[OpenAICompatibleEmbedding, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request, len(seen))

    values: dict[str, Any] = {"embedding_mode": "live", "embedding_api_key": "sk-embed-test", **settings}
    http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    return OpenAICompatibleEmbedding(make_settings(**values), http, backoff_base_seconds=0.0), seen


async def test_success_orders_by_index_and_sends_auth_and_dimensions() -> None:
    client, seen = _client(_ok)
    vectors = await client.embed(["a", "b", "c"])
    assert [v[0] for v in vectors] == [1.0, 2.0, 3.0]
    [request] = seen
    assert str(request.url) == "https://api.openai.com/v1/embeddings"
    assert request.headers["authorization"] == "Bearer sk-embed-test"
    assert json.loads(request.content) == {
        "model": "text-embedding-3-small",
        "input": ["a", "b", "c"],
        "dimensions": DIM,
    }
    assert await client.embed([]) == []
    assert len(seen) == 1  # 空入力では API を呼ばない


async def test_dimensions_parameter_only_for_models_that_support_it() -> None:
    client, seen = _client(_ok, embedding_model="text-embedding-ada-002")
    await client.embed(["a"])
    assert "dimensions" not in json.loads(seen[0].content)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_retryable_status_is_retried(status: int) -> None:
    def handler(request: httpx.Request, attempt: int) -> httpx.Response:
        return httpx.Response(status, text="busy") if attempt == 1 else _ok(request, attempt)

    client, seen = _client(handler)
    assert len(await client.embed(["a"])) == 1
    assert len(seen) == 2


async def test_transport_error_is_retried_then_reported() -> None:
    def handler(request: httpx.Request, _attempt: int) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client, seen = _client(handler, embedding_max_retries=2)
    with pytest.raises(EmbeddingError) as exc:
        await client.embed(["a"])
    assert len(seen) == 3
    assert exc.value.attempts == 3
    assert exc.value.status_code is None
    assert "ConnectError" in str(exc.value)


async def test_retries_are_bounded_for_persistent_5xx() -> None:
    client, seen = _client(lambda _r, _n: httpx.Response(502, text="bad gateway"))
    with pytest.raises(EmbeddingError) as exc:
        await client.embed(["a"])
    assert len(seen) == 2  # EMBEDDING_MAX_RETRIES（既定 1）+ 1
    assert exc.value.status_code == 502
    assert exc.value.attempts == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
async def test_client_errors_are_not_retried(status: int) -> None:
    client, seen = _client(lambda _r, _n: httpx.Response(status, json={"error": {"message": "input too long"}}))
    with pytest.raises(EmbeddingError) as exc:
        await client.embed(["a"])
    assert len(seen) == 1
    assert exc.value.status_code == status
    assert exc.value.attempts == 1
    assert str(exc.value).startswith(f"HTTP {status}:")
    assert "sk-embed-test" not in str(exc.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>gateway</html>"),  # JSON でない
        httpx.Response(200, json={"object": "list"}),  # data が無い
        httpx.Response(200, json=[1, 2, 3]),  # 形が違う
        httpx.Response(200, json={"data": [{"embedding": _vector(1.0)}]}),  # index が無い
        httpx.Response(200, json={"data": [{"index": 0, "embedding": None}]}),
        httpx.Response(200, json={"data": [{"index": 0, "embedding": ["x"] * DIM}]}),
    ],
)
async def test_malformed_response_is_an_embedding_error(response: httpx.Response) -> None:
    client, seen = _client(lambda _r, _n: response)
    with pytest.raises(EmbeddingError, match="invalid embeddings response"):
        await client.embed(["a"])
    assert len(seen) == 1  # 形の違う 200 は再試行しない


@pytest.mark.parametrize(
    "data",
    [
        [{"index": 0, "embedding": [0.1] * 768}],  # 次元違い（DB は vector(1536)）
        [],  # 件数不足
        [{"index": 0, "embedding": _vector(1.0)}, {"index": 1, "embedding": _vector(2.0)}],  # 件数過多
    ],
)
async def test_unexpected_shape_is_rejected(data: list[dict[str, Any]]) -> None:
    client, _ = _client(lambda _r, _n: httpx.Response(200, json={"data": data}))
    with pytest.raises(EmbeddingError, match="unexpected shape"):
        await client.embed(["a"])


def test_live_mode_builds_the_http_client_and_requires_a_key() -> None:
    http = httpx.AsyncClient()
    live = create_embedding_client(make_settings(embedding_mode="live", embedding_api_key="sk-x"), http)
    assert isinstance(live, OpenAICompatibleEmbedding)
    assert live.model_name == "text-embedding-3-small"
    assert live.dimensions == DIM
    hashed = create_embedding_client(make_settings(), http)
    assert hashed.model_name == f"hash-ngram-{DIM}"
    settings = make_settings()
    with pytest.raises(ValueError, match="EMBEDDING_API_KEY"):
        OpenAICompatibleEmbedding(settings, http)
