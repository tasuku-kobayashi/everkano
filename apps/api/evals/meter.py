"""LLM 呼び出しの計測（用途ごとのトークン・費用）と費用の上限（--max-cost-jpy）。

アプリの LLMClient を包んで create_app(llm=...) に渡す。エンジンの全用途（chat / memory_analysis / memory_summary /
affinity_eval / proactive_message / feed_caption）と、ハーネス自身の用途（sim_user / eval_judge）を記録する。

トークン数:
- live: API の usage（prompt_tokens / completion_tokens。DeepSeek 直結なら prompt_cache_hit_tokens）
- mock: 文字数 × tokens_per_char（仮定。既定 1.0 = 日本語 1 文字 ≒ 1 トークンの保守的な見積もり）。
  `token_counter`（--tokenizer: 本番のモデルのトークナイザ）があれば、本文をそれで数える（tokens_source=tokenizer）
キャッシュ（DeepSeek のプレフィックスキャッシュ）: usage に無ければ、同じ用途の直前のプロンプトとの共通の先頭部分
（静的なペルソナ・指示）をキャッシュに当たる分として見積もる（64 トークン単位に切り捨て）。
費用 = (入力 − キャッシュ) × input + キャッシュ × cached_input + 出力 × output（円 / 100 万トークン）。
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import AsyncGenerator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Literal

from app.services.llm import LLMClient, LLMError, LLMRequest, LLMResult, LLMStream, stream_completion
from evals.tokenizer import MESSAGE_OVERHEAD_TOKENS, TokenCounter

CACHE_BLOCK_TOKENS: Final[int] = 64
_LCP_HISTORY: Final[int] = 8
# 評価ハーネス自身の用途（1 ユーザーあたりの本番のコストには含めない）
HARNESS_PURPOSES: Final[frozenset[str]] = frozenset({"sim_user", "eval_judge"})


class CostCapExceededError(LLMError):
    """費用の上限を超えた（live の実行を止める）。"""

    def __init__(self, spent: float, cap: float) -> None:
        super().__init__(f"eval cost cap exceeded: ¥{spent:.1f} > ¥{cap:.1f}", retryable=False)
        self.spent = spent
        self.cap = cap


@dataclass(frozen=True, slots=True)
class Price:
    input: float
    cached_input: float
    output: float


class PriceTable:
    def __init__(self, table: Mapping[str, Mapping[str, float]], *, default_model: str) -> None:
        self._table = {k: Price(v["input"], v.get("cached_input", v["input"]), v["output"]) for k, v in table.items()}
        if not self._table:
            raise ValueError("empty price table")
        self.default_model = default_model if default_model in self._table else next(iter(self._table))
        self.unknown_models: set[str] = set()

    def price_for(self, model: str) -> Price:
        found = self._table.get(model)
        if found is None:
            self.unknown_models.add(model)
            return self._table[self.default_model]
        return found

    def to_dict(self) -> dict[str, dict[str, float]]:
        return {
            k: {"input": p.input, "cached_input": p.cached_input, "output": p.output} for k, p in self._table.items()
        }


@dataclass(slots=True)
class CallRecord:
    purpose: str
    model: str
    prompt_chars: int
    completion_chars: int
    prompt_tokens: float
    completion_tokens: float
    cached_tokens: float
    cost_jpy: float
    latency_ms: float
    streamed: bool
    sim_time: datetime | None
    tokens_source: Literal["usage", "estimate", "tokenizer"]
    ok: bool = True
    mode: str = ""  # engine / baseline（1 回の実行で両方のモードの記録を持つ）
    text: str = ""  # 生成された本文（E2 の生成時点の検査用。chat / proactive_message / feed_caption のみ保持）
    cached_chars: int = 0  # 同じ用途の直前のプロンプトとの共通の先頭（文字数。トークン数の仮定を変えた試算に使う）
    price: Price | None = None  # 費用の計算に使った単価

    def cost_at(self, tokens_per_char: float) -> float:
        """文字数 × tokens_per_char でトークン数を見積もった場合の費用（感度の試算用）。"""
        price = self.price
        if price is None:
            return self.cost_jpy
        cached = _floor_block(self.cached_chars * tokens_per_char)
        prompt = self.prompt_chars * tokens_per_char
        completion = self.completion_chars * tokens_per_char
        return ((prompt - cached) * price.input + cached * price.cached_input + completion * price.output) / 1_000_000

    def to_dict(self) -> dict[str, object]:
        return {
            "purpose": self.purpose,
            "model": self.model,
            "prompt_chars": self.prompt_chars,
            "completion_chars": self.completion_chars,
            "prompt_tokens": round(self.prompt_tokens, 1),
            "completion_tokens": round(self.completion_tokens, 1),
            "cached_tokens": round(self.cached_tokens, 1),
            "cost_jpy": round(self.cost_jpy, 5),
            "latency_ms": round(self.latency_ms, 1),
            "tokens_source": self.tokens_source,
            "ok": self.ok,
        }


_KEEP_TEXT: Final[frozenset[str]] = frozenset({"chat", "proactive_message", "feed_caption", "comment_reply"})


def _prompt_text(request: LLMRequest) -> str:
    return "\n".join(f"<{m['role']}>{m['content']}" for m in request.messages)


def _lcp(a: str, b: str) -> int:
    limit = min(len(a), len(b))
    i = 0
    while i < limit and a[i] == b[i]:
        i += 1
    return i


@dataclass(slots=True)
class PurposeTotals:
    calls: int = 0
    prompt_tokens: float = 0.0
    completion_tokens: float = 0.0
    cached_tokens: float = 0.0
    cost_jpy: float = 0.0
    prompt_chars: int = 0
    completion_chars: int = 0
    errors: int = 0

    def to_dict(self) -> dict[str, float | int]:
        return {
            "calls": self.calls,
            "prompt_tokens": round(self.prompt_tokens, 1),
            "completion_tokens": round(self.completion_tokens, 1),
            "cached_tokens": round(self.cached_tokens, 1),
            "cost_jpy": round(self.cost_jpy, 4),
            "prompt_chars": self.prompt_chars,
            "completion_chars": self.completion_chars,
            "errors": self.errors,
            "avg_prompt_tokens": round(self.prompt_tokens / self.calls, 1) if self.calls else 0.0,
            "avg_completion_tokens": round(self.completion_tokens / self.calls, 1) if self.calls else 0.0,
        }


@dataclass(slots=True)
class Meter:
    """呼び出しの記録と費用の集計（複数のモードで共有できる）。"""

    prices: PriceTable
    tokens_per_char: float = 1.0
    max_cost_jpy: float | None = None  # live のときだけ上限を適用する
    price_model: str | None = None  # mock の見積もりに使うモデル名（None = 呼び出しのモデル）
    token_counter: TokenCounter | None = None  # mock の見積もりに使うトークナイザ（None = 文字数 × tokens_per_char）
    records: list[CallRecord] = field(default_factory=list)
    label: str = ""
    _recent: dict[str, deque[str]] = field(default_factory=dict)

    @property
    def spent_jpy(self) -> float:
        return sum(r.cost_jpy for r in self.records)

    @property
    def cap_exceeded(self) -> bool:
        return self.max_cost_jpy is not None and self.spent_jpy > self.max_cost_jpy

    def check_cap(self) -> None:
        if self.max_cost_jpy is not None and self.spent_jpy > self.max_cost_jpy:
            raise CostCapExceededError(self.spent_jpy, self.max_cost_jpy)

    def cached_prefix_chars(self, purpose: str, prompt: str) -> int:
        recent = self._recent.setdefault(purpose, deque(maxlen=_LCP_HISTORY))
        best = max((_lcp(prompt, previous) for previous in recent), default=0)
        recent.append(prompt)
        return best

    def record(
        self,
        request: LLMRequest,
        *,
        model: str,
        text: str,
        usage: Mapping[str, int] | None,
        latency_ms: float,
        streamed: bool,
        sim_time: datetime | None,
        ok: bool = True,
    ) -> CallRecord:
        prompt = _prompt_text(request)
        prompt_chars = sum(len(m["content"]) for m in request.messages)
        completion_chars = len(text)
        lcp_chars = self.cached_prefix_chars(request.purpose, prompt)
        live_usage = usage is not None and model != "mock-persona-v1" and "prompt_tokens" in usage
        if live_usage and usage is not None:
            prompt_tokens = float(usage.get("prompt_tokens", 0))
            completion_tokens = float(usage.get("completion_tokens", 0))
            if "prompt_cache_hit_tokens" in usage:
                cached = float(usage["prompt_cache_hit_tokens"])
            else:
                ratio = lcp_chars / prompt_chars if prompt_chars else 0.0
                cached = _floor_block(prompt_tokens * min(ratio, 1.0))
            source: Literal["usage", "estimate", "tokenizer"] = "usage"
        elif self.token_counter is not None:
            count = self.token_counter
            prompt_tokens = float(sum(count(m["content"]) + MESSAGE_OVERHEAD_TOKENS for m in request.messages))
            completion_tokens = float(count(text))
            cached = _floor_block(min(float(count(prompt[:lcp_chars])), prompt_tokens)) if lcp_chars else 0.0
            source = "tokenizer"
        else:
            prompt_tokens = prompt_chars * self.tokens_per_char
            completion_tokens = completion_chars * self.tokens_per_char
            cached = _floor_block(min(lcp_chars, prompt_chars) * self.tokens_per_char)
            source = "estimate"
        # mock の見積もりは、本番で使うモデル（price_model）の価格で計算する
        priced_model = (self.price_model or model) if source != "usage" else model
        price = self.prices.price_for(priced_model)
        cost = (
            (prompt_tokens - cached) * price.input + cached * price.cached_input + completion_tokens * price.output
        ) / 1_000_000
        record = CallRecord(
            purpose=request.purpose,
            model=model,
            prompt_chars=prompt_chars,
            completion_chars=completion_chars,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached,
            cost_jpy=cost,
            latency_ms=latency_ms,
            streamed=streamed,
            sim_time=sim_time,
            tokens_source=source,
            ok=ok,
            mode=self.label,
            text=text if request.purpose in _KEEP_TEXT else "",
            cached_chars=min(lcp_chars, prompt_chars),
            price=price,
        )
        self.records.append(record)
        return record

    def for_mode(self, mode: str) -> list[CallRecord]:
        return [r for r in self.records if r.mode == mode]

    def totals(self, *, include_harness: bool = True, mode: str | None = None) -> dict[str, PurposeTotals]:
        out: dict[str, PurposeTotals] = {}
        for r in self.records:
            if not include_harness and r.purpose in HARNESS_PURPOSES:
                continue
            if mode is not None and r.mode != mode:
                continue
            t = out.setdefault(r.purpose, PurposeTotals())
            t.calls += 1
            t.prompt_tokens += r.prompt_tokens
            t.completion_tokens += r.completion_tokens
            t.cached_tokens += r.cached_tokens
            t.cost_jpy += r.cost_jpy
            t.prompt_chars += r.prompt_chars
            t.completion_chars += r.completion_chars
            t.errors += 0 if r.ok else 1
        return out


def _floor_block(tokens: float) -> float:
    return float(int(tokens // CACHE_BLOCK_TOKENS) * CACHE_BLOCK_TOKENS)


class MeteredLLM:
    """LLMClient を包んで呼び出しを Meter に記録する（live では費用の上限を超えたら LLMError で止める）。"""

    def __init__(self, inner: LLMClient, meter: Meter, *, clock: Callable[[], datetime] | None = None) -> None:
        self._inner = inner
        self._meter = meter
        self._clock = clock

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    @property
    def meter(self) -> Meter:
        return self._meter

    def _now(self) -> datetime | None:
        return self._clock() if self._clock is not None else None

    async def complete(self, request: LLMRequest) -> LLMResult:
        self._meter.check_cap()
        started = time.perf_counter()
        try:
            result = await self._inner.complete(request)
        except LLMError:
            self._meter.record(
                request,
                model=self.model_name,
                text="",
                usage=None,
                latency_ms=(time.perf_counter() - started) * 1000,
                streamed=False,
                sim_time=self._now(),
                ok=False,
            )
            raise
        self._meter.record(
            request,
            model=result.model,
            text=result.text,
            usage=result.usage,
            latency_ms=(time.perf_counter() - started) * 1000,
            streamed=False,
            sim_time=self._now(),
        )
        return result

    def stream(self, request: LLMRequest) -> LLMStream:
        self._meter.check_cap()
        inner = stream_completion(self._inner, request)
        outer = LLMStream(model=inner.model)
        meter = self._meter
        sim_time = self._now()

        async def chunks() -> AsyncGenerator[str, None]:
            started = time.perf_counter()
            pieces: list[str] = []
            ok = False
            try:
                async for piece in inner:
                    pieces.append(piece)
                    yield piece
                ok = True
            finally:
                await inner.aclose()
                outer.model = inner.model
                outer.usage = inner.usage
                meter.record(
                    request,
                    model=inner.model,
                    text="".join(pieces),
                    usage=inner.usage,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    streamed=True,
                    sim_time=sim_time,
                    ok=ok,
                )

        return outer.bind(chunks())
