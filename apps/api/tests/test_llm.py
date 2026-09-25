from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.services.llm import (
    LLMError,
    LLMRequest,
    MockHints,
    MockLLM,
    OpenAICompatibleLLM,
    clean_reply,
    current_activity,
    memory_core,
    mock_extract,
    parse_json_object,
    pick_relevant_memory,
)
from app.services.memory import parse_candidates, parse_summary
from app.services.persona import load_persona_file
from app.services.types import RetrievedMemory
from tests.conftest import FIXTURES_DIR, make_settings

PERSONA = load_persona_file(FIXTURES_DIR / "personas" / "test_persona.yaml")
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)  # JST 木曜 21:00


def _memory(content: str, *, similarity: float = 0.2, tags: tuple[str, ...] = ()) -> RetrievedMemory:
    return RetrievedMemory(
        id=uuid.uuid4(), content=content, importance=0.7, tags=tags, similarity=similarity, created_at=NOW
    )


def _chat_request(message: str, memories: tuple[RetrievedMemory, ...] = ()) -> LLMRequest:
    return LLMRequest(
        purpose="chat",
        messages=[{"role": "user", "content": message}],
        temperature=0.8,
        max_tokens=100,
        hints=MockHints(persona=PERSONA, now=NOW, user_message=message, memories=memories),
    )


async def test_mock_chat_is_deterministic_and_in_character() -> None:
    llm = MockLLM()
    a = await llm.complete(_chat_request("今日はいい天気だね"))
    b = await llm.complete(_chat_request("今日はいい天気だね"))
    assert a.text == b.text
    assert a.model == "mock-persona-v1"
    assert a.usage is not None
    assert a.usage["total_tokens"] > 0
    # 口調例のいずれかが含まれる（キャラらしさ）
    assert any(example in a.text for example in PERSONA.speech.examples)
    replies = {(await llm.complete(_chat_request(f"メッセージ{i}"))).text for i in range(8)}
    assert len(replies) > 1


async def test_mock_chat_references_relevant_memory() -> None:
    memory = _memory("ユーザーは「来週、大阪に出張するんだ」と話していた")
    unrelated = _memory("ユーザーは「猫が好きなんだ」と話していた", similarity=0.9)
    result = await MockLLM().complete(_chat_request("大阪のお土産、何がいいと思う？", (unrelated, memory)))
    assert "来週、大阪に出張する" in result.text
    none = await MockLLM().complete(_chat_request("お昼ごはん食べた？", (memory,)))
    assert "出張" not in none.text


async def test_mock_chat_secret_memory_and_time_awareness() -> None:
    secret = _memory("ユーザーは猫を飼っている", tags=("secret",))
    result = await MockLLM().complete(_chat_request("猫の写真見る？", (secret,)))
    assert "二人だけの秘密" in result.text
    question = await MockLLM().complete(_chat_request("今なにしてるの？"))
    # JST 木曜 21:00 → 平日の「19:00退社」の後
    assert "退社" in question.text


def test_current_activity_weekday_and_weekend() -> None:
    pattern = PERSONA.schedule_pattern
    assert current_activity(pattern, datetime(2026, 9, 24, 1, 0, tzinfo=UTC)) == "出社"  # 木 10:00 JST
    assert current_activity(pattern, datetime(2026, 9, 24, 14, 0, tzinfo=UTC)) == "リラックス"  # 木 23:00 JST
    assert current_activity(pattern, datetime(2026, 9, 26, 1, 0, tzinfo=UTC)) == "昼まで寝る"  # 土 10:00 JST


def test_mock_extract_rules() -> None:
    items = mock_extract("来週、大阪に出張するんだ。ちょっと緊張してる。今日はいい天気だね")
    assert items == [
        {"content": "ユーザーは「来週、大阪に出張するんだ」と話していた", "importance": 0.95, "category": "personal"},
        {"content": "ユーザーは「ちょっと緊張してる」と話していた", "importance": 0.55, "category": "emotion"},
    ]
    # 質問・あいさつ・雑談は抽出しない
    assert mock_extract("明日なにする？") == []
    assert mock_extract("お昼ごはん食べた？") == []
    assert mock_extract("今日はいい天気だね") == []
    emotion_only = mock_extract("ちょっと緊張してる")
    assert len(emotion_only) == 1
    assert emotion_only[0]["importance"] < 0.6


async def test_mock_extraction_output_parses() -> None:
    request = LLMRequest(
        purpose="memory_extraction",
        messages=[{"role": "user", "content": "x"}],
        temperature=0,
        max_tokens=100,
        json_mode=True,
        hints=MockHints(persona=PERSONA, now=NOW, user_message="猫が好きなんだ"),
    )
    result = await MockLLM().complete(request)
    candidates = parse_candidates(result.text)
    assert [c.content for c in candidates] == ["ユーザーは「猫が好きなんだ」と話していた"]
    assert candidates[0].importance >= 0.6


def test_parse_candidates_is_defensive() -> None:
    text = '説明です\n```json\n{"memories": [{"content": " ユーザーは犬派 ", "importance": 1.7}, {"content": ""},'
    text += ' {"content": "x", "importance": "abc"}, "bad"]}\n```'
    candidates = parse_candidates(text)
    assert [(c.content, c.importance) for c in candidates] == [("ユーザーは犬派", 1.0)]
    assert parse_candidates("not json") == []
    assert parse_candidates('[{"content": "ユーザーは早起き", "importance": 0.8}]')[0].importance == 0.8
    assert parse_summary('{"summary": "要約"}') == "要約"
    assert parse_summary("JSONではない要約") == "JSONではない要約"


def test_parse_json_object_variants() -> None:
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('前置き {"a": 2} 後置き') == {"a": 2}
    with pytest.raises(ValueError, match="no JSON"):
        parse_json_object("none")


def test_memory_core_and_relevance() -> None:
    assert memory_core("ユーザーは「来週、大阪に出張するんだ」と話していた") == "来週、大阪に出張する"
    assert memory_core("ユーザーは猫を飼っている。") == "猫を飼っている"
    summary = _memory("大阪の話をした", tags=("summary",))
    assert pick_relevant_memory("大阪行きたい", [summary]) is None


def test_clean_reply() -> None:
    assert clean_reply("テスト美咲: 「やっほー」", "テスト美咲", max_chars=100) == "やっほー"
    assert clean_reply("a" * 10, "x", max_chars=5) == "aaaa…"


# --------------------------------------------------------------------------- live client (MockTransport)


def _live(handler: Any, **overrides: Any) -> tuple[OpenAICompatibleLLM, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request, len(seen))

    settings = make_settings(llm_mode="live", llm_api_key="sk-test", llm_max_retries=2, **overrides)
    http = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    return OpenAICompatibleLLM(settings, http, backoff_base_seconds=0.0), seen


def _ok(content: str = "こんにちは") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "deepseek/deepseek-chat",
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )


def _request(json_mode: bool = False) -> LLMRequest:
    return LLMRequest(
        purpose="chat",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.8,
        max_tokens=50,
        json_mode=json_mode,
    )


async def test_live_llm_success_and_headers() -> None:
    llm, seen = _live(lambda _r, _n: _ok())
    result = await llm.complete(_request())
    assert result.text == "こんにちは"
    assert result.usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    req = seen[0]
    assert str(req.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer sk-test"
    assert req.headers["x-title"] == "everkano"
    body = json.loads(req.content)
    assert body["model"] == "deepseek/deepseek-chat"
    assert body["max_tokens"] == 50


async def test_live_llm_retries_on_5xx_and_429() -> None:
    def handler(_r: httpx.Request, n: int) -> httpx.Response:
        if n == 1:
            return httpx.Response(503, text="busy")
        if n == 2:
            return httpx.Response(429, headers={"retry-after": "0"}, text="slow down")
        return _ok("ok")

    llm, seen = _live(handler)
    assert (await llm.complete(_request())).text == "ok"
    assert len(seen) == 3


async def test_live_llm_gives_up_after_retries() -> None:
    llm, seen = _live(lambda _r, _n: httpx.Response(500, text="down"))
    with pytest.raises(LLMError) as exc:
        await llm.complete(_request())
    assert exc.value.status_code == 500
    assert exc.value.attempts == 3
    assert len(seen) == 3


async def test_live_llm_does_not_retry_4xx() -> None:
    llm, seen = _live(lambda _r, _n: httpx.Response(401, text="bad key"))
    with pytest.raises(LLMError):
        await llm.complete(_request())
    assert len(seen) == 1


async def test_live_llm_timeout_is_retried() -> None:
    def handler(request: httpx.Request, n: int) -> httpx.Response:
        if n < 3:
            raise httpx.ReadTimeout("timeout", request=request)
        return _ok("late")

    llm, seen = _live(handler)
    assert (await llm.complete(_request())).text == "late"
    assert len(seen) == 3


async def test_live_llm_json_mode_fallback() -> None:
    def handler(request: httpx.Request, _n: int) -> httpx.Response:
        body = json.loads(request.content)
        if "response_format" in body:
            return httpx.Response(400, text="response_format not supported")
        return _ok('{"memories": []}')

    llm, seen = _live(handler)
    assert (await llm.complete(_request(json_mode=True))).text == '{"memories": []}'
    assert len(seen) == 2


async def test_live_llm_empty_content_is_error() -> None:
    llm, _ = _live(lambda _r, _n: _ok("  "))
    with pytest.raises(LLMError, match="empty"):
        await llm.complete(_request())
