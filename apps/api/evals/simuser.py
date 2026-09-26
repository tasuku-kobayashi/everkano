"""シミュレーションユーザーの発言（mock: 台本の文を seed で決定的に選ぶ / live: LLM が意図を言い換える）。"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, Protocol

from app.services.llm import LLMClient, LLMError, LLMRequest, parse_json_object
from evals.prompts import PromptTemplate
from evals.scenarios.base import SimUser, Utterance
from evals.textutil import contains_any

SIM_PURPOSE: Final[str] = "sim_user"
MAX_MESSAGE_CHARS: Final[int] = 200
HISTORY_TURNS: Final[int] = 6

PhraseSource = Literal["template", "llm", "fallback"]


@dataclass(frozen=True, slots=True)
class Phrased:
    text: str
    source: PhraseSource
    error: str | None = None


def template_choice(utterance: Utterance, seed: int) -> str:
    rng = random.Random(f"{seed}:{utterance.user}:{utterance.seq}")
    return rng.choice(utterance.texts)


class Phraser(Protocol):
    name: str

    async def phrase(
        self, utterance: Utterance, user: SimUser, history: Sequence[tuple[str, str]], text: str
    ) -> Phrased: ...


class TemplatePhraser:
    """台本の文をそのまま使う（mock。text はハーネスが seed で選んだ台本の文）。"""

    name = "template"

    async def phrase(
        self, utterance: Utterance, user: SimUser, history: Sequence[tuple[str, str]], text: str
    ) -> Phrased:
        _ = (utterance, user, history)  # Phraser の共通の引数（台本の文だけを使う）
        return Phrased(text, "template")


class LLMPhraser:
    """意図を人物像に合わせて言い換える（docs/eval/prompts/sim_user.md）。verbatim の発言は言い換えない。"""

    def __init__(self, llm: LLMClient, prompts: Mapping[str, PromptTemplate], *, model: str | None = None) -> None:
        self._llm = llm
        self._template = prompts["sim_user"]
        self._model = model
        self.name = f"llm:{model or llm.model_name}"
        self.fallbacks = 0

    async def phrase(
        self, utterance: Utterance, user: SimUser, history: Sequence[tuple[str, str]], text: str
    ) -> Phrased:
        if utterance.verbatim:
            return Phrased(text, "template")
        recent = "\n".join(f"{who}: {body}" for who, body in history[-HISTORY_TURNS * 2 :]) or "（まだ会話はない）"
        values = {
            "profile": user.profile,
            "history": recent,
            "intent": utterance.intent,
            "template": text,
            "must_include": "、".join(utterance.must_include) or "（なし）",
            "must_not_include": "、".join(utterance.must_not_include) or "（なし）",
        }
        request = LLMRequest(
            purpose=SIM_PURPOSE,
            messages=self._template.render(values),
            temperature=0.7,
            max_tokens=200,
            json_mode=True,
            model=self._model,
            mock_context={"template": text},
        )
        try:
            result = await self._llm.complete(request)
            data = parse_json_object(result.text)
            message = str(data.get("message", "")).strip() if isinstance(data, dict) else ""
            problem = validate_phrase(message, utterance)
            if problem is not None:
                raise ValueError(problem)
        except (LLMError, ValueError) as exc:
            self.fallbacks += 1
            return Phrased(text, "fallback", f"{type(exc).__name__}: {exc}"[:200])
        return Phrased(message, "llm")


def validate_phrase(message: str, utterance: Utterance) -> str | None:
    """言い換えが評価の条件を守っているか（守れていなければ理由）。"""
    if not message:
        return "empty message"
    if len(message) > MAX_MESSAGE_CHARS:
        return "too long"
    missing = [t for t in utterance.must_include if not contains_any(message, (t,))]
    if missing:
        return f"missing required terms {missing}"
    leaked = contains_any(message, utterance.must_not_include)
    if leaked:
        return f"contains forbidden terms {leaked}"
    return None
