"""1 アクティブユーザーあたりの月間 LLM コストの推計（E7: ¥100 前後）。

シミュレーション（engine モード）で記録した呼び出しから、用途ごとの単価を求め、利用の多さ（1 日の発言数）ごとに
30 日分に換算する:
  月額 = 30 × { 発言数/日 × (chat の 1 回 + 返答後の分析の 1 発言あたり) + 自発メッセージの 1 アクティブ日あたり }
- chat の 1 回: 会話が長くなって履歴が予算の上限に近づいた後半（シミュレーション時刻の後半 50%）の平均（定常状態）
- 返答後の分析（memory_analysis + affinity_eval + memory_summary）: 合計 ÷ ユーザーの発言数
  （シミュレーションは発言の間隔が 1〜4 分なので、デバウンス（既定 180 秒・最大 18 分）でまとまる数は少なめ = 保守的）
- 自発メッセージ: 合計 ÷ アクティブなユーザー日数
- フィードのキャプション: キャラごとの固定費（ユーザー数で割られる）なので 1 ユーザーの額には含めず、別に示す
- sim_user / eval_judge（評価ハーネス自身）は含めない
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from evals.meter import CallRecord
from evals.records import ModeRecord

PROFILES: Final[dict[str, int]] = {"light": 5, "median": 15, "heavy": 40}
MONTH_DAYS: Final[int] = 30
ANALYSIS_PURPOSES: Final[tuple[str, ...]] = ("memory_analysis", "affinity_eval", "memory_summary")
BUDGET_JPY: Final[float] = 100.0
SENSITIVITY_TPC: Final[tuple[float, ...]] = (0.6, 0.8, 1.0)


@dataclass(frozen=True, slots=True)
class CostProjection:
    chat_per_turn: float
    analysis_per_turn: float
    analysis_calls_per_turn: float
    proactive_per_active_day: float
    caption_per_character_day: float
    user_turns: int
    active_user_days: int
    profiles: dict[str, float]
    chat_prompt_tokens: float
    chat_cached_share: float
    tokens_source: str
    tokens_per_char: float | None = None  # mock の見積もりに使った値（live・トークナイザでは None）
    # 文字数 × トークン数の仮定（0.6 / 0.8 / 1.0）で数え直した月額（mock のみ。live では空）
    char_sensitivity: dict[str, dict[str, float]] = field(default_factory=dict)
    # トークナイザで数えたときの、用途ごとの文字あたりのトークン数（入力 / 出力）
    measured_tokens_per_char: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chat_per_turn_jpy": round(self.chat_per_turn, 4),
            "analysis_per_turn_jpy": round(self.analysis_per_turn, 4),
            "analysis_calls_per_turn": round(self.analysis_calls_per_turn, 2),
            "proactive_per_active_day_jpy": round(self.proactive_per_active_day, 4),
            "caption_per_character_day_jpy": round(self.caption_per_character_day, 4),
            "user_turns": self.user_turns,
            "active_user_days": self.active_user_days,
            "profiles_jpy_per_month": {k: round(v, 1) for k, v in self.profiles.items()},
            "chat_prompt_tokens_steady": round(self.chat_prompt_tokens, 1),
            "chat_cached_share": round(self.chat_cached_share, 3),
            "tokens_source": self.tokens_source,
            "tokens_per_char": self.tokens_per_char,
            "sensitivity_tokens_per_char": self.sensitivity(),
            "measured_tokens_per_char": self.measured_tokens_per_char,
        }

    def sensitivity(self) -> dict[str, dict[str, float]]:
        """mock の見積もりで、トークン数の仮定（文字あたり）を変えたときの月額。

        記録の文字数（入力・出力・キャッシュに当たる先頭）から数え直す（64 トークン単位の切り捨てを含む）。
        """
        if self.char_sensitivity:
            return self.char_sensitivity
        if self.tokens_per_char is None or self.tokens_per_char <= 0:
            return {}
        return {
            f"{tpc:.1f}": {k: round(v * tpc / self.tokens_per_char, 1) for k, v in self.profiles.items()}
            for tpc in SENSITIVITY_TPC
        }


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _profiles(
    engine: Sequence[CallRecord],
    steady: Sequence[CallRecord],
    *,
    user_turns: int,
    active_days: int,
    cost: Callable[[CallRecord], float],
) -> tuple[dict[str, float], float, float, float]:
    """(月額の利用の多さごとの内訳, chat の 1 回, 分析の 1 発言あたり, 自発メッセージの 1 日あたり)。"""
    chat_cost = _mean([cost(r) for r in steady])
    analysis = [r for r in engine if r.purpose in ANALYSIS_PURPOSES]
    analysis_per_turn = sum(cost(r) for r in analysis) / user_turns if user_turns else 0.0
    proactive_cost = sum(cost(r) for r in engine if r.purpose == "proactive_message")
    proactive_per_day = proactive_cost / active_days if active_days else 0.0
    profiles = {
        name: MONTH_DAYS * (turns * (chat_cost + analysis_per_turn) + proactive_per_day)
        for name, turns in PROFILES.items()
    }
    return profiles, chat_cost, analysis_per_turn, proactive_per_day


def _measured_tpc(engine: Sequence[CallRecord]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    purposes = sorted({r.purpose for r in engine if r.tokens_source == "tokenizer"})
    for purpose in [*purposes, "all"]:
        rows = [r for r in engine if r.tokens_source == "tokenizer" and purpose in (r.purpose, "all")]
        prompt_chars = sum(r.prompt_chars for r in rows)
        completion_chars = sum(r.completion_chars for r in rows)
        if not rows or not prompt_chars:
            continue
        out[purpose] = {
            "input": round(sum(r.prompt_tokens for r in rows) / prompt_chars, 3),
            "output": round(sum(r.completion_tokens for r in rows) / completion_chars, 3) if completion_chars else 0.0,
            "cached_share": round(sum(r.cached_tokens for r in rows) / max(sum(r.prompt_tokens for r in rows), 1), 3),
        }
    return out


def project(
    records: Sequence[CallRecord], mode: ModeRecord, *, characters: int, tokens_per_char: float | None = None
) -> CostProjection:
    engine = [r for r in records if r.purpose not in ("sim_user", "eval_judge")]
    chat = sorted((r for r in engine if r.purpose == "chat"), key=lambda r: r.sim_time.timestamp() if r.sim_time else 0)
    steady = chat[len(chat) // 2 :] or chat
    user_turns = sum(1 for t in mode.turns if t.error is None)
    active_days = len({(t.user, t.day) for t in mode.turns if t.error is None})
    profiles, chat_cost, analysis_per_turn, proactive_per_day = _profiles(
        engine, steady, user_turns=user_turns, active_days=active_days, cost=lambda r: r.cost_jpy
    )
    analysis_calls = (
        sum(1 for r in engine if r.purpose in ANALYSIS_PURPOSES and r.purpose != "memory_summary") / user_turns
        if user_turns
        else 0.0
    )
    caption_cost = sum(r.cost_jpy for r in engine if r.purpose == "feed_caption")
    caption_per_character_day = caption_cost / (characters * mode.days) if characters and mode.days else 0.0
    prompt_tokens = _mean([r.prompt_tokens for r in steady])
    cached = _mean([r.cached_tokens for r in steady])
    sources = {r.tokens_source for r in engine}
    char_sensitivity: dict[str, dict[str, float]] = {}
    if engine and "usage" not in sources:
        for tpc in SENSITIVITY_TPC:
            at_tpc, *_ = _profiles(
                engine,
                steady,
                user_turns=user_turns,
                active_days=active_days,
                cost=lambda r, tpc=tpc: r.cost_at(tpc),  # type: ignore[misc]
            )
            char_sensitivity[f"{tpc:.1f}"] = {k: round(v, 1) for k, v in at_tpc.items()}
    return CostProjection(
        chat_per_turn=chat_cost,
        analysis_per_turn=analysis_per_turn,
        analysis_calls_per_turn=analysis_calls,
        proactive_per_active_day=proactive_per_day,
        caption_per_character_day=caption_per_character_day,
        user_turns=user_turns,
        active_user_days=active_days,
        profiles=profiles,
        chat_prompt_tokens=prompt_tokens,
        chat_cached_share=cached / prompt_tokens if prompt_tokens else 0.0,
        tokens_source="/".join(sorted(sources)) or "none",
        tokens_per_char=tokens_per_char if sources == {"estimate"} else None,
        char_sensitivity=char_sensitivity,
        measured_tokens_per_char=_measured_tpc(engine),
    )
