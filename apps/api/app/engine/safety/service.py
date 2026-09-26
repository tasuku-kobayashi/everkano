"""E6 の安全対応（SafetyService の実装）と、キャラの声で返す返答の組み立て。

チャットのパイプラインは Gate #1 より前に `assess()` し、検知したら LLM を使わずに `build_reply()` の文面を返す
（好感度の評価からも外す）。文面・窓口は `packages/prompts/safety/resources.ja.yaml`。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from app.engine.safety.detector import SelfHarmDetector
from app.engine.safety.resources import SafetyConfig
from app.engine.types import SafetyAssessment, SafetyResource
from app.services.persona import Persona

POLITE_TONE_MARKERS: Final[tuple[str, ...]] = ("敬語", "丁寧")


def format_resource_line(resource: SafetyResource) -> str:
    parts = [resource.name]
    if resource.phone:
        parts.append(resource.phone)
    text = " ".join(parts)
    if resource.hours:
        text += f"（{resource.hours}）"
    if resource.url and not resource.phone:
        text += f" {resource.url}"
    return f"・{text}"


class DefaultSafetyService:
    """SafetyService（app/engine/types.py）の実装。決定的で LLM を使わない。"""

    def __init__(self, config: SafetyConfig, detector: SelfHarmDetector | None = None) -> None:
        self._config = config
        self._detector = detector or SelfHarmDetector()
        self._resources = config.resource_items()

    def assess(self, text: str) -> SafetyAssessment:
        return self._detector.assess(text)

    def resources(self) -> Sequence[SafetyResource]:
        return self._resources

    def build_reply(self, persona: Persona, *, call_user: str | None = None) -> str:
        """キャラの口調（一人称・呼び方・敬語かどうか）で、気づかいの言葉と相談窓口を返す。"""
        speech = persona.speech
        polite = any(marker in (speech.tone or "") for marker in POLITE_TONE_MARKERS)
        template = self._config.messages.polite if polite else self._config.messages.casual
        inline = self._resources[: self._config.inline_resources]
        values = {
            "first_person": speech.first_person,
            "second_person": call_user or speech.second_person,
            "resources": "\n".join(format_resource_line(r) for r in inline),
        }
        text = template
        for key, value in values.items():
            text = text.replace("{" + key + "}", value)
        return "\n".join(line for line in text.splitlines() if line.strip())
