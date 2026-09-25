"""`packages/prompts/templates/*.ja.txt` を読み込み、LLM に渡すメッセージ列へ描画する（仕様 §8.2）。

テンプレート書式（詳細は packages/prompts/README.md）:
- `{placeholder}`（英小文字・数字・_）を値で置換する。JSON 例の `{"key": ...}` は置換対象外。
- 行 `=== user ===` があれば、それより前を system メッセージ、後ろを user メッセージとして扱う。
- 起動時に必須プレースホルダの有無と未知のプレースホルダを検証する（typo を早期検出）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Literal, TypedDict
from zoneinfo import ZoneInfo

from app.services.persona import Persona
from app.services.types import HistoryItem, RetrievedMemory

JST: Final = ZoneInfo("Asia/Tokyo")
SECRET_MARKER: Final[str] = "（二人だけの秘密）"  # noqa: S105 - 表示用マーカー
SUMMARY_MARKER: Final[str] = "（これまでの会話の要約）"
USER_SEPARATOR: Final[str] = "=== user ==="
_PLACEHOLDER_RE: Final = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
_WEEKDAYS_JA: Final[tuple[str, ...]] = ("月", "火", "水", "木", "金", "土", "日")

# 抽出・要約で LLM に渡す会話ログの上限（トークン節約）
EXTRACTION_CONTEXT_MESSAGES: Final[int] = 6
TRANSCRIPT_MESSAGE_MAX_CHARS: Final[int] = 300
TRANSCRIPT_MAX_CHARS: Final[int] = 12000

Role = Literal["system", "user", "assistant"]


class ChatMessage(TypedDict):
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    required: frozenset[str]
    optional: frozenset[str] = frozenset()


TEMPLATE_SPECS: Final[dict[str, TemplateSpec]] = {
    "dm_system": TemplateSpec(
        required=frozenset({"name", "profile", "speech", "relationship", "memories", "short_term"}),
        optional=frozenset({"first_person", "second_person", "schedule", "now", "archetype", "bio"}),
    ),
    "memory_extraction": TemplateSpec(
        required=frozenset({"recent_context", "user_message"}),
        optional=frozenset({"name", "memory_focus", "second_person"}),
    ),
    "memory_summary": TemplateSpec(
        required=frozenset({"conversation"}),
        optional=frozenset({"name"}),
    ),
    "comment_reply": TemplateSpec(
        required=frozenset({"name", "post_caption", "comment_body"}),
        optional=frozenset({"bio", "speech", "comment_style", "profile", "first_person", "second_person"}),
    ),
}


class PromptTemplateError(Exception):
    """テンプレートの読込・検証エラー（起動を中止する）。"""


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    name: str
    system: str
    user: str | None

    @property
    def placeholders(self) -> frozenset[str]:
        text = self.system + (self.user or "")
        return frozenset(_PLACEHOLDER_RE.findall(text))

    def render(self, values: Mapping[str, str]) -> list[ChatMessage]:
        messages: list[ChatMessage] = [{"role": "system", "content": _substitute(self.system, values)}]
        if self.user is not None:
            messages.append({"role": "user", "content": _substitute(self.user, values)})
        return messages


def _substitute(text: str, values: Mapping[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise PromptTemplateError(f"missing value for placeholder '{{{key}}}'")
        return values[key]

    return _PLACEHOLDER_RE.sub(repl, text).strip()


def parse_template(name: str, text: str) -> PromptTemplate:
    lines = text.splitlines()
    if USER_SEPARATOR in (line.strip() for line in lines):
        index = next(i for i, line in enumerate(lines) if line.strip() == USER_SEPARATOR)
        system = "\n".join(lines[:index])
        user: str | None = "\n".join(lines[index + 1 :])
    else:
        system, user = text, None
    template = PromptTemplate(name=name, system=system, user=user)
    spec = TEMPLATE_SPECS.get(name)
    if spec is not None:
        found = template.placeholders
        missing = spec.required - found
        unknown = found - spec.required - spec.optional
        if missing:
            raise PromptTemplateError(f"{name}: 必須プレースホルダがありません: {sorted(missing)}")
        if unknown:
            raise PromptTemplateError(f"{name}: 未知のプレースホルダがあります: {sorted(unknown)}")
    return template


# ---------------------------------------------------------------------------
# 描画ヘルパー
# ---------------------------------------------------------------------------


def format_now(now: datetime) -> str:
    local = now.astimezone(JST)
    return f"{local.year}年{local.month}月{local.day}日（{_WEEKDAYS_JA[local.weekday()]}）{local:%H:%M}"


def render_speech(persona: Persona) -> str:
    s = persona.speech
    lines: list[str] = []
    if s.tone:
        lines.append(f"- 口調: {s.tone}")
    if s.sentence_length:
        lines.append(f"- 文の長さ: {s.sentence_length}")
    if s.emoji:
        lines.append(f"- 絵文字: {s.emoji}")
    lines.append(f"- 一人称: 「{s.first_person}」 / 相手の呼び方: 「{s.second_person}」")
    if s.examples:
        lines.append("- 話し方の例:")
        lines.extend(f"  - 「{ex}」" for ex in s.examples)
    if s.ng_words:
        lines.append("- 絶対に使わない言葉: " + "、".join(f"「{w}」" for w in s.ng_words))
    return "\n".join(lines)


def render_relationship(persona: Persona) -> str:
    r = persona.relationship
    if not r.initial and not r.progression:
        return "（特になし）"
    return f"- はじめの距離感: {r.initial}\n- 関係の変化: {r.progression}"


def render_memory_line(memory: RetrievedMemory) -> str:
    prefix = ""
    if memory.is_summary:
        prefix += SUMMARY_MARKER
    if memory.is_secret:
        prefix += SECRET_MARKER
    return f"- {prefix}{memory.content}"


def render_memories(memories: Sequence[RetrievedMemory]) -> str:
    if not memories:
        return "（まだ特にない）"
    return "\n".join(render_memory_line(m) for m in memories)


def render_schedule(persona: Persona, now: datetime) -> str:
    lines = [f"現在は{format_now(now)}（日本時間）。"]
    if persona.schedule_pattern:
        lines.append("あなたの普段の過ごし方:")
        lines.append(persona.schedule_pattern.strip())
    return "\n".join(lines)


def render_short_term_note(history: Sequence[HistoryItem]) -> str:
    # 直近の会話そのものは system の後ろに user / assistant メッセージとして渡す（重複させない）
    if not history:
        return "（まだ会話はありません。これが二人の最初のやりとりです）"
    return (
        f"直近{len(history)}件のやりとりは、このあとの会話履歴（user = 相手 / assistant = あなた）"
        "として渡されます。流れを踏まえて自然に続けてください。"
    )


def render_transcript(
    history: Sequence[HistoryItem],
    persona: Persona,
    *,
    per_message_max: int = TRANSCRIPT_MESSAGE_MAX_CHARS,
    total_max: int = TRANSCRIPT_MAX_CHARS,
) -> str:
    lines: list[str] = []
    for item in history:
        speaker = "ユーザー" if item.sender_type == "user" else persona.name
        body = item.body.replace("\n", " ").strip()
        if len(body) > per_message_max:
            body = body[:per_message_max] + "…"
        lines.append(f"{speaker}: {body}")
    # 上限を超える場合は新しい側を優先して残す
    total = 0
    kept: list[str] = []
    for line in reversed(lines):
        total += len(line) + 1
        if total > total_max:
            break
        kept.append(line)
    kept.reverse()
    return "\n".join(kept) if kept else "（なし）"


def _merge_consecutive(messages: list[ChatMessage]) -> list[ChatMessage]:
    """同じ role が連続する場合は結合する（role の交互性を要求するプロバイダ対策）。"""
    merged: list[ChatMessage] = []
    for message in messages:
        if merged and merged[-1]["role"] == message["role"] and message["role"] != "system":
            merged[-1] = {
                "role": message["role"],
                "content": merged[-1]["content"] + "\n" + message["content"],
            }
        else:
            merged.append(message)
    return merged


# ---------------------------------------------------------------------------
# PromptBuilder
# ---------------------------------------------------------------------------


class PromptBuilder:
    def __init__(self, templates: Mapping[str, PromptTemplate]) -> None:
        missing = set(TEMPLATE_SPECS) - set(templates)
        if missing:
            raise PromptTemplateError(f"テンプレートが見つかりません: {sorted(missing)}")
        self._templates = dict(templates)

    @classmethod
    def load_dir(cls, directory: Path) -> PromptBuilder:
        templates: dict[str, PromptTemplate] = {}
        for name in TEMPLATE_SPECS:
            path = directory / f"{name}.ja.txt"
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise PromptTemplateError(f"{path}: 読み込めません: {exc}") from exc
            templates[name] = parse_template(name, text)
        return cls(templates)

    def chat_messages(
        self,
        persona: Persona,
        *,
        memories: Sequence[RetrievedMemory],
        history: Sequence[HistoryItem],
        user_message: str,
        now: datetime,
    ) -> list[ChatMessage]:
        values = {
            "name": persona.name,
            "profile": persona.profile.strip(),
            "speech": render_speech(persona),
            "relationship": render_relationship(persona),
            "memories": render_memories(memories),
            "short_term": render_short_term_note(history),
            "first_person": persona.speech.first_person,
            "second_person": persona.speech.second_person,
            "schedule": render_schedule(persona, now),
            "now": format_now(now),
            "archetype": persona.archetype,
            "bio": persona.bio.strip(),
        }
        messages = self._templates["dm_system"].render(values)
        for item in history:
            role: Role = "user" if item.sender_type == "user" else "assistant"
            messages.append({"role": role, "content": item.body})
        messages.append({"role": "user", "content": user_message})
        return _merge_consecutive(messages)

    def extraction_messages(
        self, persona: Persona, *, history: Sequence[HistoryItem], user_message: str
    ) -> list[ChatMessage]:
        recent = history[-EXTRACTION_CONTEXT_MESSAGES:]
        focus = "\n".join(f"- {f}" for f in persona.memory_focus) or "（特になし）"
        values = {
            "name": persona.name,
            "memory_focus": focus,
            "second_person": persona.speech.second_person,
            "recent_context": render_transcript(recent, persona),
            "user_message": user_message,
        }
        return self._templates["memory_extraction"].render(values)

    def summary_messages(self, persona: Persona, *, transcript: Sequence[HistoryItem]) -> list[ChatMessage]:
        values = {"name": persona.name, "conversation": render_transcript(transcript, persona)}
        return self._templates["memory_summary"].render(values)

    def comment_reply_messages(
        self, persona: Persona, *, post_caption: str | None, comment_body: str
    ) -> list[ChatMessage]:
        values = {
            "name": persona.name,
            "bio": (persona.bio or persona.profile).strip(),
            "profile": persona.profile.strip(),
            "speech": render_speech(persona),
            "comment_style": persona.comment_style or "短く、親しみを込めて返す",
            "first_person": persona.speech.first_person,
            "second_person": persona.speech.second_person,
            "post_caption": (post_caption or "（キャプションなし）").strip(),
            "comment_body": comment_body.strip(),
        }
        return self._templates["comment_reply"].render(values)
