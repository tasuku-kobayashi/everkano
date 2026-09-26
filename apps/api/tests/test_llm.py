from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.engine.memory.summary import parse_summary
from app.services.llm import (
    MOCK_REPLY_WITHOUT_HINTS,
    LLMError,
    LLMRequest,
    MockHints,
    MockLLM,
    OpenAICompatibleLLM,
    clean_reply,
    content_tokens,
    current_activity,
    memory_core,
    mock_chat_reply,
    parse_json_object,
    parse_schedule_days,
    pick_relevant_memory,
    strip_trailing,
)
from app.services.persona import load_persona_file
from app.services.types import RetrievedMemory
from tests.conftest import FIXTURES_DIR, REPO_ROOT, make_settings

PERSONAS_DIR = REPO_ROOT / "packages" / "personas"

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


async def test_mock_without_registered_handler_or_hints() -> None:
    """用途別のハンドラもヒントも無い呼び出しは決まった返事（MVP の memory_extraction の用途は廃止）。"""
    request = LLMRequest(
        purpose="memory_extraction",
        messages=[{"role": "user", "content": "x"}],
        temperature=0,
        max_tokens=100,
        json_mode=True,
    )
    result = await MockLLM().complete(request)
    assert result.text == MOCK_REPLY_WITHOUT_HINTS


async def test_mock_memory_summary_uses_the_engine_handler() -> None:
    """中期要約のモックは記憶モジュールが登録したハンドラ（app.engine.memory.mock）が返す。"""
    import app.engine.memory  # noqa: F401, PLC0415 - import 時にモックのハンドラを登録する

    request = LLMRequest(
        purpose="memory_summary",
        messages=[{"role": "user", "content": "x"}],
        temperature=0,
        max_tokens=100,
        json_mode=True,
        mock_context={"transcript": [{"sender": "user", "body": "来週、大阪に出張するんだ"}]},
    )
    result = await MockLLM().complete(request)
    assert "大阪に出張" in parse_summary(result.text)


def test_parse_summary_variants() -> None:
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


def test_mock_memory_text_keeps_long_vowels_and_word_endings() -> None:
    assert memory_core("ユーザーは「趣味はサッカー」と話していた") == "趣味はサッカー"
    assert memory_core("ユーザーは「好きな飲み物はコーヒー」と話していた") == "好きな飲み物はコーヒー"
    assert memory_core("ユーザーは「猫が好きなの」と話していた") == "猫が好き"
    assert memory_core("ユーザーは「好きな魚はさかな」と話していた") == "好きな魚はさかな"
    assert strip_trailing("楽しかったーーー！！w") == "楽しかった"
    assert strip_trailing("サッカーーー！") == "サッカー"
    assert strip_trailing("面白かった（笑）") == "面白かった"
    assert strip_trailing("昨日は爆笑") == "昨日は爆笑"
    assert strip_trailing("new") == "new"


def test_memory_relevance_ignores_time_words() -> None:
    # 時間表現だけの重なりでは記憶を持ち出さない
    assert "今日" not in content_tokens("今日は雨だったね")
    assert content_tokens("来週大阪に出張") >= {"大阪", "出張"}
    tired = _memory("ユーザーは「今日も仕事で疲れたよ」と話していた")
    assert pick_relevant_memory("今日は雨だったね", [tired]) is None
    assert pick_relevant_memory("仕事やめたい", [tired]) is tired


def test_parse_schedule_days() -> None:
    assert parse_schedule_days("平日（水族館の出勤日。木〜月）:") == frozenset({3, 4, 5, 6, 0})
    assert parse_schedule_days("平日（金〜水）:") == frozenset({4, 5, 6, 0, 1, 2})
    assert parse_schedule_days("休日（火曜・水曜）:") == frozenset({1, 2})
    assert parse_schedule_days("休日（ライブの日。主に土日）:") == frozenset({5, 6})
    assert parse_schedule_days("平日（サロン出勤日。火曜以外）:") == frozenset({0, 2, 3, 4, 5, 6})
    assert parse_schedule_days("休日（七日に一度の安息日）:") is None
    assert parse_schedule_days("平日:") is None


def test_current_activity_follows_persona_workdays() -> None:
    osananajimi = load_persona_file(PERSONAS_DIR / "osananajimi.yaml")  # 出勤: 木〜月 / 休み: 火・水
    tsundere = load_persona_file(PERSONAS_DIR / "tsundere.yaml")  # 出勤: 金〜水 / 休み: 木
    saturday_15 = datetime(2026, 9, 26, 6, 0, tzinfo=UTC)
    thursday_15 = datetime(2026, 9, 24, 6, 0, tzinfo=UTC)
    tuesday_15 = datetime(2026, 9, 22, 6, 0, tzinfo=UTC)
    assert current_activity(osananajimi.schedule_pattern, saturday_15) == "展示・ふれあいガイド"
    assert current_activity(osananajimi.schedule_pattern, tuesday_15) == "午後は兄の漁の手伝いか、町の食堂で定食"
    assert current_activity(tsundere.schedule_pattern, thursday_15) == "食べ歩き・製菓道具の専門店めぐり"
    assert current_activity(tsundere.schedule_pattern, saturday_15) == "遅めのまかない"


def test_mock_reaction_only_answers_activity_when_asked() -> None:
    misaki = load_persona_file(PERSONAS_DIR / "ol_oneesan.yaml")
    noon = datetime(2026, 9, 24, 3, 30, tzinfo=UTC)  # JST 木 12:30 → 「同僚とランチ」
    question = mock_chat_reply(MockHints(persona=misaki, now=noon, user_message="美咲はお酒好き？"))
    assert "ランチ" not in question
    assert "酒" in question
    statement = mock_chat_reply(MockHints(persona=misaki, now=noon, user_message="好きな食べ物はマグロの刺身なの"))
    assert "ランチ" not in statement
    assert "マグロ" in statement
    activity = mock_chat_reply(MockHints(persona=misaki, now=noon, user_message="今なにしてるの？"))
    assert "同僚とランチ" in activity
    # 書き出しが同じ口調例（「わたし？…」）を続けない
    assert activity.count(f"{misaki.speech.first_person}？") <= 1
    elf = load_persona_file(PERSONAS_DIR / "isekai_elf.yaml")
    night = datetime(2026, 9, 24, 13, 0, tzinfo=UTC)  # JST 22:00
    elf_reply = mock_chat_reply(MockHints(persona=elf, now=night, user_message="今なにしてるの？"))
    assert "時間」の時間" not in elf_reply


def test_mock_recall_dates_old_relative_memories() -> None:
    old = RetrievedMemory(
        id=uuid.uuid4(),
        content="ユーザーは「来週大阪に出張するんだ」と話していた",
        importance=0.9,
        tags=(),
        similarity=0.4,
        created_at=NOW - timedelta(days=20),
    )
    reply = mock_chat_reply(MockHints(persona=PERSONA, now=NOW, user_message="大阪のおすすめある？", memories=(old,)))
    assert "9月4日に来週大阪に出張する" in reply
    fresh = RetrievedMemory(
        id=old.id, content=old.content, importance=0.9, tags=(), similarity=0.4, created_at=NOW - timedelta(hours=1)
    )
    same_day = mock_chat_reply(
        MockHints(persona=PERSONA, now=NOW, user_message="大阪のおすすめある？", memories=(fresh,))
    )
    assert "月" not in same_day.split("来週")[0]


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
