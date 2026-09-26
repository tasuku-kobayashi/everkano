"""評価ハーネス: LLM 呼び出しの計測（トークン・キャッシュの見積もり・費用）と費用の上限。"""

from __future__ import annotations

import pytest

from app.services.llm import LLMError, LLMRequest, LLMResult, MockLLM
from evals.meter import CostCapExceededError, Meter, MeteredLLM, PriceTable

PRICES = {"deepseek/deepseek-chat": {"input": 40.0, "cached_input": 11.0, "output": 165.0}}


def _request(content: str, purpose: str = "chat") -> LLMRequest:
    return LLMRequest(
        purpose=purpose,
        messages=[{"role": "system", "content": content}, {"role": "user", "content": "こんにちは"}],
        temperature=0.0,
        max_tokens=50,
    )


def test_price_table_falls_back_for_unknown_models() -> None:
    table = PriceTable(PRICES, default_model="deepseek/deepseek-chat")
    assert table.price_for("other").input == 40.0
    assert table.unknown_models == {"other"}


def test_mock_estimate_uses_chars_and_prefix_cache() -> None:
    meter = Meter(PriceTable(PRICES, default_model="deepseek/deepseek-chat"), tokens_per_char=1.0)
    static = "あ" * 200
    first = meter.record(
        _request(static + "い" * 10),
        model="mock-persona-v1",
        text="x" * 20,
        usage=None,
        latency_ms=1,
        streamed=False,
        sim_time=None,
    )
    assert first.prompt_tokens == 215  # system 210 文字 + user 5 文字
    assert first.cached_tokens == 0  # 最初の呼び出しはキャッシュに当たらない
    second = meter.record(
        _request(static + "う" * 10),
        model="mock-persona-v1",
        text="x" * 20,
        usage=None,
        latency_ms=1,
        streamed=False,
        sim_time=None,
    )
    assert second.cached_tokens == 192  # 共通の先頭 200 文字 → 64 トークン単位に切り捨て
    expected = ((215 - 192) * 40 + 192 * 11 + 20 * 165) / 1_000_000
    assert second.cost_jpy == pytest.approx(expected)
    assert second.tokens_source == "estimate"
    half = Meter(PriceTable(PRICES, default_model="deepseek/deepseek-chat"), tokens_per_char=0.5)
    record = half.record(
        _request("a" * 100), model="mock-persona-v1", text="", usage=None, latency_ms=1, streamed=False, sim_time=None
    )
    assert record.prompt_tokens == pytest.approx(52.5)


def test_live_usage_is_used_when_present() -> None:
    meter = Meter(PriceTable(PRICES, default_model="deepseek/deepseek-chat"))
    record = meter.record(
        _request("x" * 1000),
        model="deepseek/deepseek-chat",
        text="ok",
        usage={"prompt_tokens": 300, "completion_tokens": 20, "prompt_cache_hit_tokens": 256},
        latency_ms=1,
        streamed=True,
        sim_time=None,
    )
    assert record.tokens_source == "usage"
    assert record.cached_tokens == 256
    assert record.cost_jpy == pytest.approx(((300 - 256) * 40 + 256 * 11 + 20 * 165) / 1_000_000)


async def test_metered_llm_records_complete_and_stream() -> None:
    meter = Meter(PriceTable(PRICES, default_model="deepseek/deepseek-chat"))
    llm = MeteredLLM(MockLLM(), meter)
    request = _request("あなたはテスト用のキャラクターです。")
    result = await llm.complete(
        LLMRequest(purpose="eval_judge", messages=request.messages, temperature=0, max_tokens=10)
    )
    assert result.text
    chunks = [c async for c in llm.stream(request)]
    assert "".join(chunks)
    purposes = [r.purpose for r in meter.records]
    assert purposes == ["eval_judge", "chat"]
    assert meter.records[1].streamed
    assert meter.records[1].completion_chars == len("".join(chunks))
    totals = meter.totals(include_harness=False)
    assert set(totals) == {"chat"}


class _ExpensiveLLM:
    model_name = "deepseek/deepseek-chat"

    async def complete(self, request: LLMRequest) -> LLMResult:
        return LLMResult(
            text="ok", model=self.model_name, latency_ms=1, usage={"prompt_tokens": 10_000_000, "completion_tokens": 0}
        )


async def test_cost_cap_stops_further_calls() -> None:
    meter = Meter(PriceTable(PRICES, default_model="deepseek/deepseek-chat"), max_cost_jpy=100.0)
    llm = MeteredLLM(_ExpensiveLLM(), meter)  # type: ignore[arg-type]
    await llm.complete(_request("x"))  # ¥400 を使う（呼び出しの前は上限内）
    assert meter.cap_exceeded
    with pytest.raises(CostCapExceededError):
        await llm.complete(_request("x"))
    with pytest.raises(LLMError):
        llm.stream(_request("x"))


def test_token_counter_replaces_the_char_estimate_and_char_sensitivity_is_kept() -> None:
    """--tokenizer: 本文をトークナイザで数える（ここでは 2 文字 = 1 トークンの偽物）。文字数の試算も残す。"""

    def half(text: str) -> int:
        return len(text) // 2

    meter = Meter(PriceTable(PRICES, default_model="deepseek/deepseek-chat"), token_counter=half)
    static = "あ" * 400
    meter.record(
        _request(static + "い"),
        model="mock-persona-v1",
        text="",
        usage=None,
        latency_ms=1,
        streamed=False,
        sim_time=None,
    )
    record = meter.record(
        _request(static + "う"),
        model="mock-persona-v1",
        text="x" * 40,
        usage=None,
        latency_ms=1,
        streamed=False,
        sim_time=None,
    )
    assert record.tokens_source == "tokenizer"
    # system 401 文字 → 200 + user 5 文字 → 2、役割のトークン 2 × 2
    assert record.prompt_tokens == 200 + 2 + 2 * 2
    assert record.completion_tokens == 20
    assert record.cached_tokens == 192  # 共通の先頭（"<system>" + 400 文字 = 408 文字 → 204 トークン）を 64 単位に
    assert record.cached_chars == record.prompt_chars  # 共通の先頭（役割の目印を含む）は本文の文字数で頭打ち
    # 文字数 × 1.0 で数え直した費用（感度の試算）
    expected = ((406 - 384) * 40 + 384 * 11 + 40 * 165) / 1_000_000
    assert record.cost_at(1.0) == pytest.approx(expected)
