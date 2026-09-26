"""好感度の評価（LLM 用途 `affinity_eval`。A4 / A9 / A10）。

- 返答生成とは別の、隔離された LLM 呼び出し（temperature 0・JSON モード）。プロンプトは会話の本文をすべて
  「データ」として扱い、観察できる振る舞いだけを軸ごとに -2〜+2 の整数で採点させる
  （テンプレート: packages/prompts/templates/affinity_eval.ja.txt）。
- 会話の本文は JSON 文字列として埋め込み、`<` `>` もエスケープする（本文に `</turns>` や見出しを書いて
  プロンプトの構造を偽造させない）。
- 出力はスキーマで検証し、失敗したら検証エラーを添えて 1 回だけ再試行する。
- E1 / A9: 評価の入力（AffinityEvalInput）は会話の本文とペルソナの説明だけ。
  課金・購入・トークン残高などのデータは受け取らない（そのフィールドを持たない。
  tests/engine/affinity/test_e1_structure.py で検査する）。
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.engine.affinity.config import AffinityConfig
from app.engine.types import AFFINITY_AXES
from app.services.llm import LLMClient, LLMRequest, parse_json_object
from app.services.persona import Persona
from app.services.prompt import (
    ChatMessage,
    PromptTemplate,
    PromptTemplateError,
    one_line,
    parse_template,
    register_template_spec,
)

PURPOSE: Final[str] = "affinity_eval"
TEMPLATE_NAME: Final[str] = "affinity_eval"
TEMPLATE_PLACEHOLDERS: Final[frozenset[str]] = frozenset({"name", "persona_notes", "possessiveness_rule", "turns"})
REASON_MAX_CHARS: Final[int] = 60

# 起動時（PromptBuilder.load_dir）にテンプレートの有無とプレースホルダを検証させる
register_template_spec(TEMPLATE_NAME, required=sorted(TEMPLATE_PLACEHOLDERS))

Score = Annotated[int, Field(ge=-2, le=2)]


class EvalTurn(BaseModel):
    """評価器に渡す 1 往復（会話の本文だけ）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int
    user: str
    character: str


class AffinityEvalInput(BaseModel):
    """評価器の入力のすべて（E1: 会話の本文とペルソナの説明以外のデータを持たない）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    persona_name: str
    persona_notes: str
    possessiveness_enabled: bool
    turns: list[EvalTurn]


class TurnScore(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int = Field(ge=0)
    closeness: Score = 0
    trust: Score = 0
    romance: Score = 0
    awkwardness: Score = 0
    discontent: Score = 0
    possessiveness: Score = 0
    reason: str = ""

    def scores(self) -> dict[str, int]:
        return {axis: int(getattr(self, axis)) for axis in AFFINITY_AXES}


class EvalOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    turns: list[TurnScore]


@dataclass(frozen=True, slots=True)
class EvalOutcome:
    scores: dict[int, TurnScore]
    model: str
    usage: dict[str, int]
    latency_ms: int
    attempts: int


class EvaluationError(Exception):
    """評価の出力が再試行後も検証に通らなかった。"""

    def __init__(self, message: str, *, raw: str, model: str, usage: dict[str, int], attempts: int) -> None:
        super().__init__(message)
        self.raw = raw
        self.model = model
        self.usage = usage
        self.attempts = attempts


def load_template(prompts_dir: Path) -> PromptTemplate:
    path = prompts_dir / f"{TEMPLATE_NAME}.ja.txt"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptTemplateError(f"{path}: 読み込めません: {exc}") from exc
    template = parse_template(TEMPLATE_NAME, text)
    found = template.placeholders
    if found != TEMPLATE_PLACEHOLDERS:
        raise PromptTemplateError(
            f"{TEMPLATE_NAME}: プレースホルダが一致しません（不足: {sorted(TEMPLATE_PLACEHOLDERS - found)}, "
            f"未知: {sorted(found - TEMPLATE_PLACEHOLDERS)}）"
        )
    return template


def _clip(text: str, limit: int) -> str:
    value = one_line(text)
    return value if len(value) <= limit else value[:limit] + "…"


def build_input(
    persona: Persona | None, persona_name: str, turns: Sequence[tuple[str, str]], config: AffinityConfig
) -> AffinityEvalInput:
    """(ユーザー発言, キャラ返答) の列 → 評価の入力。"""
    notes = persona.engine.affinity.notes if persona is not None and persona.engine is not None else "特になし"
    possessive = bool(
        persona is not None and persona.engine is not None and persona.engine.affinity.sensitivity.possessiveness > 0
    )
    return AffinityEvalInput(
        persona_name=persona_name,
        persona_notes=one_line(notes),
        possessiveness_enabled=possessive,
        turns=[
            EvalTurn(index=i, user=_clip(user, config.max_user_chars), character=_clip(reply, config.max_reply_chars))
            for i, (user, reply) in enumerate(turns)
        ],
    )


def _escape_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")


def render_messages(template: PromptTemplate, data: AffinityEvalInput) -> list[ChatMessage]:
    possessiveness_rule = (
        "相手が他の人との親密な予定・恋愛の話をした → +1。安心させる言葉・一緒に過ごす約束 → -1。それ以外は 0"
        if data.possessiveness_enabled
        else "常に 0（このキャラでは使わない）"
    )
    turns = "\n".join(_escape_json(turn.model_dump()) for turn in data.turns)
    return template.render(
        {
            "name": data.persona_name,
            "persona_notes": data.persona_notes,
            "possessiveness_rule": possessiveness_rule,
            "turns": turns,
        }
    )


def validate_output(text: str, expected: set[int]) -> dict[int, TurnScore]:
    parsed = parse_json_object(text)
    output = EvalOutput.model_validate(parsed)
    scores: dict[int, TurnScore] = {}
    for item in output.turns:
        if item.index not in expected:
            raise ValueError(f"unknown turn index: {item.index}")
        if item.index in scores:
            raise ValueError(f"duplicate turn index: {item.index}")
        scores[item.index] = item.model_copy(update={"reason": one_line(item.reason)[:REASON_MAX_CHARS]})
    missing = expected - set(scores)
    if missing:
        raise ValueError(f"missing turn indexes: {sorted(missing)}")
    return scores


def _add_usage(total: dict[str, int], usage: dict[str, int] | None) -> None:
    for key, value in (usage or {}).items():
        total[key] = total.get(key, 0) + int(value)


async def evaluate(
    llm: LLMClient, template: PromptTemplate, data: AffinityEvalInput, config: AffinityConfig
) -> EvalOutcome:
    """LLM で採点する。検証に失敗したら 1 回だけ再試行する（LLMError はそのまま送出）。"""
    messages = render_messages(template, data)
    expected = {turn.index for turn in data.turns}
    usage: dict[str, int] = {}
    started = time.perf_counter()
    raw = ""
    model = ""
    error: Exception | None = None
    for attempt in (1, 2):
        request = LLMRequest(
            purpose=PURPOSE,
            messages=messages,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            json_mode=True,
            model=config.model,
            mock_context=data.model_dump(),
        )
        result = await llm.complete(request)
        _add_usage(usage, result.usage)
        raw, model = result.text, result.model
        try:
            scores = validate_output(result.text, expected)
        except (ValueError, ValidationError) as exc:
            error = exc
            messages = [
                *messages,
                {"role": "assistant", "content": result.text[:2000]},
                {
                    "role": "user",
                    "content": (
                        f"出力が形式に合いません（{str(exc)[:300]}）。"
                        "指定の JSON だけを、すべてのターンの index について出力し直してください。"
                    ),
                },
            ]
            continue
        return EvalOutcome(
            scores=scores,
            model=model,
            usage=usage,
            latency_ms=int((time.perf_counter() - started) * 1000),
            attempts=attempt,
        )
    raise EvaluationError(str(error), raw=raw[:500], model=model, usage=usage, attempts=2)
