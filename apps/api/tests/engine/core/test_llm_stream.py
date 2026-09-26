"""LLM のストリーミング（OpenAI 互換の SSE の解析・再試行 / MockLLM の分割）と用途別のモデル。"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest

from app.services.llm import (
    LLMError,
    LLMRequest,
    LLMResult,
    MockLLM,
    OpenAICompatibleLLM,
    create_llm_client,
    stream_completion,
)
from tests.conftest import make_settings


def _request(purpose: str = "chat", model: str | None = None) -> LLMRequest:
    return LLMRequest(
        purpose=purpose, messages=[{"role": "user", "content": "hi"}], temperature=0.8, max_tokens=50, model=model
    )


def _sse(*events: dict[str, Any] | str) -> bytes:
    lines = [": OPENROUTER PROCESSING", ""]
    for event in events:
        data = event if isinstance(event, str) else json.dumps(event, ensure_ascii=False)
        lines += [f"data: {data}", ""]
    return ("\n".join(lines) + "\n").encode()


def _delta(text: str) -> dict[str, Any]:
    return {"model": "deepseek/deepseek-chat-v3", "choices": [{"index": 0, "delta": {"content": text}}]}


def _live(handler: Any, **overrides: Any) -> OpenAICompatibleLLM:
    settings = make_settings(llm_mode="live", llm_api_key="sk-test", llm_max_retries=2, **overrides)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatibleLLM(settings, http, backoff_base_seconds=0.001)


async def test_openai_stream_parses_chunks_usage_and_model() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        usage = {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 5, "prompt_cache_hit_tokens": 64}}
        return httpx.Response(
            200,
            content=_sse(_delta("おか"), _delta("えり！"), {"choices": [{"delta": {}}]}, usage, "[DONE]"),
            headers={"content-type": "text/event-stream"},
        )

    llm = _live(handler)
    stream = llm.stream(_request())
    chunks = [c async for c in stream]
    assert chunks == ["おか", "えり！"]
    assert stream.usage == {"prompt_tokens": 100, "completion_tokens": 5, "prompt_cache_hit_tokens": 64}
    assert stream.model == "deepseek/deepseek-chat-v3"
    assert stream.first_chunk_ms is not None
    assert stream.latency_ms is not None
    assert bodies[0]["stream"] is True
    assert bodies[0]["stream_options"] == {"include_usage": True}


async def test_openai_stream_retries_before_first_chunk_and_drops_unsupported_options() -> None:
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return httpx.Response(503, text="overloaded")
        if "stream_options" in body:
            return httpx.Response(400, text="stream_options is not supported")
        return httpx.Response(200, content=_sse(_delta("ok"), "[DONE]"))

    llm = _live(handler)
    assert [c async for c in llm.stream(_request())] == ["ok"]
    assert len(calls) == 3
    assert "stream_options" not in calls[-1]


async def test_openai_stream_error_mid_stream_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=_sse(_delta("途中"), {"error": {"message": "provider crashed"}}))

    llm = _live(handler)
    received: list[str] = []

    async def consume() -> None:
        async for chunk in llm.stream(_request()):
            received.append(chunk)

    with pytest.raises(LLMError, match="stream error"):
        await consume()
    assert received == ["途中"]
    assert calls == 1


async def test_openai_stream_gives_up_after_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    with pytest.raises(LLMError) as info:
        [c async for c in _live(handler).stream(_request())]
    assert info.value.status_code == 429
    assert info.value.attempts == 3


async def test_purpose_models_are_used_when_request_has_no_model() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body["model"])
        if body.get("stream"):
            return httpx.Response(200, content=_sse(_delta("x"), "[DONE]"))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "model": body["model"]})

    llm = _live(
        handler,
        llm_model_analysis="deepseek/deepseek-chat-cheap",
        llm_model_proactive="proactive-model",
        llm_model_caption="caption-model",
    )
    await llm.complete(_request("memory_analysis"))
    await llm.complete(_request("affinity_eval"))
    await llm.complete(_request("proactive_message"))
    await llm.complete(_request("feed_caption"))
    await llm.complete(_request("affinity_eval", model="explicit"))
    [c async for c in llm.stream(_request("chat"))]
    assert seen == [
        "deepseek/deepseek-chat-cheap",
        "deepseek/deepseek-chat-cheap",
        "proactive-model",
        "caption-model",
        "explicit",
        "deepseek/deepseek-chat",
    ]


async def test_mock_stream_matches_complete_and_honours_delay() -> None:
    llm = MockLLM(stream_delay_ms=20)
    request = _request()
    whole = (await llm.complete(request)).text
    started = time.perf_counter()
    stream = llm.stream(request)
    chunks = [c async for c in stream]
    elapsed = time.perf_counter() - started
    assert "".join(chunks) == whole
    assert len(chunks) >= 2
    assert all(len(c) <= 3 for c in chunks)
    assert elapsed >= 0.02 * len(chunks) * 0.8
    assert stream.usage is not None


async def test_mock_subclass_without_init_and_plain_clients_can_stream() -> None:
    class Canned(MockLLM):
        def __init__(self) -> None:  # 親の __init__ を呼ばないテスト用の差し替え
            self.calls = 0

        async def complete(self, request: LLMRequest) -> LLMResult:
            self.calls += 1
            return LLMResult(text="こんにちは。元気？", model="canned", latency_ms=1)

    assert "".join([c async for c in Canned().stream(_request())]) == "こんにちは。元気？"

    class PlainClient:  # stream を持たない LLMClient（古いテスト用の差し替えなど）
        model_name = "plain"

        async def complete(self, request: LLMRequest) -> LLMResult:
            return LLMResult(text="まるごと", model="plain", latency_ms=1, usage={"total_tokens": 3})

    stream = stream_completion(PlainClient(), _request())  # type: ignore[arg-type]
    assert [c async for c in stream] == ["まるごと"]
    assert stream.usage == {"total_tokens": 3}


def test_create_llm_client_passes_mock_stream_delay() -> None:
    llm = create_llm_client(make_settings(llm_mock_stream_delay_ms=15), httpx.AsyncClient())
    assert isinstance(llm, MockLLM)
    assert llm._stream_delay == pytest.approx(0.015)
