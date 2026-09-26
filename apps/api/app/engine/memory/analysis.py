"""記憶の分析（`memory_analysis`）: プロンプトの組み立てと、LLM の出力（JSON）のスキーマ検証。

1 回の LLM 呼び出しで、今回のターンと関連する既存の記憶・約束から次を得る（ENGINE_BRIEF §2.6）。
- memories: add / update / supersede / noop（Mem0 の ADD / UPDATE / DELETE / NOOP。DELETE は履歴を残す supersede）
- promises: 絶対日付に直した期日付きの約束（M6）
- promise_updates: 既存の約束の mentioned / done / cancelled
- character_statements: キャラが自分について話したこと（M8）

既存の記憶・約束は UUID ではなく短い参照（m1, p1 …）で渡す（トークン節約と、ID の捏造を防ぐため）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.engine.memory.dates import date_reference
from app.engine.memory.text import format_datetime_ja, one_line
from app.engine.types import JST, TurnRecord
from app.services.persona import Persona
from app.services.prompt import ChatMessage, PromptTemplate, PromptTemplateError, parse_template

ANALYSIS_TEMPLATE: Final[str] = "memory_analysis"
ANALYSIS_REQUIRED: Final[frozenset[str]] = frozenset({"existing_memories", "pending_promises", "turns"})
ANALYSIS_OPTIONAL: Final[frozenset[str]] = frozenset(
    {"name", "first_person", "now", "date_reference", "threshold", "memory_focus"}
)
CONTENT_MAX_CHARS: Final[int] = 300
PROMISE_CONTENT_MAX_CHARS: Final[int] = 200
TURN_TEXT_MAX_CHARS: Final[int] = 1000

MemoryOpKind = Literal["fact", "preference", "episode", "promise", "emotion", "relationship"]
OpName = Literal["add", "update", "supersede", "noop"]


class AnalysisOutputError(ValueError):
    """LLM の出力が JSON でない・スキーマに合わない（1 回だけ、エラーを添えて再生成を頼む）。"""


def _clean_text(value: object, max_chars: int) -> object:
    if isinstance(value, str):
        text = one_line(value)
        return text[:max_chars] if text else None
    return value


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


class MemoryOpOut(_Out):
    op: OpName
    kind: MemoryOpKind | None = None
    content: str | None = None
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    target: str | None = Field(default=None, pattern=r"^m\d{1,3}$")
    turn: int | None = None
    secret: bool = False

    @field_validator("content", mode="before")
    @classmethod
    def _content(cls, value: object) -> object:
        return _clean_text(value, CONTENT_MAX_CHARS)

    @field_validator("importance", mode="before")
    @classmethod
    def _importance(cls, value: object) -> object:
        # 0〜1 を外れた値は丸める（1.2 → 1.0）。文字列の数値も受ける
        if isinstance(value, int | float) and not isinstance(value, bool):
            return min(max(float(value), 0.0), 1.0)
        return value

    @model_validator(mode="after")
    def _required_fields(self) -> MemoryOpOut:
        if self.op == "noop":
            return self
        if not self.content:
            raise ValueError(f"op={self.op} には content が必要です")
        if self.op in {"update", "supersede"} and self.target is None:
            raise ValueError(f"op={self.op} には target（m1 など）が必要です")
        if self.op == "add" and self.kind is None:
            raise ValueError("op=add には kind が必要です")
        return self


class PromiseOut(_Out):
    content: str = Field(min_length=1)
    memory: str | None = None
    due_date: date | None = None
    due_time: time | None = None
    due_precision: Literal["datetime", "day", "week", "month", "unknown"] = "day"
    importance: float = Field(default=0.85, ge=0.0, le=1.0)
    turn: int | None = None

    @field_validator("content", mode="before")
    @classmethod
    def _content(cls, value: object) -> object:
        return _clean_text(value, PROMISE_CONTENT_MAX_CHARS)

    @field_validator("memory", mode="before")
    @classmethod
    def _memory(cls, value: object) -> object:
        return _clean_text(value, CONTENT_MAX_CHARS)

    @field_validator("importance", mode="before")
    @classmethod
    def _importance(cls, value: object) -> object:
        if value is None:
            return 0.85
        if isinstance(value, int | float) and not isinstance(value, bool):
            return min(max(float(value), 0.0), 1.0)
        return value

    @field_validator("due_date", "due_time", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return None if value in ("", "null", "unknown") else value

    @model_validator(mode="after")
    def _precision(self) -> PromiseOut:
        # 期日の無い約束は unknown に、時刻の無い datetime は day にそろえる
        if self.due_date is None:
            self.due_precision = "unknown"
            self.due_time = None
        elif self.due_precision == "unknown":
            self.due_precision = "day"
        if self.due_precision == "datetime" and self.due_time is None:
            self.due_precision = "day"
        return self


class PromiseUpdateOut(_Out):
    target: str = Field(pattern=r"^p\d{1,3}$")
    status: Literal["mentioned", "done", "cancelled"]


class CharacterStatementOut(_Out):
    content: str = Field(min_length=1)
    occurred_date: date | None = None
    turn: int | None = None

    @field_validator("content", mode="before")
    @classmethod
    def _content(cls, value: object) -> object:
        return _clean_text(value, CONTENT_MAX_CHARS)

    @field_validator("occurred_date", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return None if value in ("", "null", "unknown") else value


class AnalysisOutput(_Out):
    memories: list[MemoryOpOut] = Field(default_factory=list)
    promises: list[PromiseOut] = Field(default_factory=list)
    promise_updates: list[PromiseUpdateOut] = Field(default_factory=list)
    character_statements: list[CharacterStatementOut] = Field(default_factory=list)

    @field_validator("memories", "promises", "promise_updates", "character_statements", mode="before")
    @classmethod
    def _null_is_empty(cls, value: object) -> object:
        return [] if value is None else value

    def truncated(self, *, max_ops: int, max_promises: int, max_statements: int) -> AnalysisOutput:
        """件数の上限を超えた分を捨てる（1 回の分析で大量の記憶を作らせない）。"""
        return AnalysisOutput(
            memories=self.memories[:max_ops],
            promises=self.promises[:max_promises],
            promise_updates=self.promise_updates[: max_promises * 2],
            character_statements=self.character_statements[:max_statements],
        )


def load_json_object(text: str) -> Any:
    """LLM の JSON 出力を寛容に読む（コードブロック・前後の説明文を許容）。読めなければ AnalysisOutputError。"""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise AnalysisOutputError("JSON オブジェクトとして読めません")


def _describe(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors(include_url=False)[:5]:
        location = ".".join(str(p) for p in error["loc"]) or "(root)"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)


def parse_analysis(text: str) -> AnalysisOutput:
    """LLM の出力を検証する。JSON でない・スキーマに合わない場合は AnalysisOutputError（理由つき）。"""
    data = load_json_object(text)
    if not isinstance(data, dict):
        raise AnalysisOutputError("トップレベルは JSON オブジェクト（{...}）にしてください")
    try:
        return AnalysisOutput.model_validate(data)
    except ValidationError as exc:
        raise AnalysisOutputError(_describe(exc)) from exc


# ---------------------------------------------------------------------------
# 分析の入力（既存の記憶・約束の参照）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MemoryRef:
    ref: str  # m1, m2 …
    id: UUID
    kind: str
    content: str
    importance: float
    is_user_edited: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PromiseRef:
    ref: str  # p1, p2 …
    id: UUID
    content: str
    due_at: datetime | None
    due_precision: str
    status: str
    event_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class AnalysisInput:
    persona: Persona
    now: datetime
    turns: Sequence[TurnRecord]
    memories: Sequence[MemoryRef]
    promises: Sequence[PromiseRef]
    threshold: float

    @property
    def memory_refs(self) -> Mapping[str, MemoryRef]:
        return {m.ref: m for m in self.memories}

    @property
    def promise_refs(self) -> Mapping[str, PromiseRef]:
        return {p.ref: p for p in self.promises}


def _date_label(value: datetime) -> str:
    local = value.astimezone(JST)
    return f"{local.year}-{local.month:02d}-{local.day:02d}"


def render_existing_memories(memories: Sequence[MemoryRef]) -> str:
    if not memories:
        return "（なし）"
    lines = []
    for memory in memories:
        mark = "（ユーザー編集）" if memory.is_user_edited else ""
        lines.append(
            f"{memory.ref}: [{memory.kind}] {one_line(memory.content)}{mark}（記録 {_date_label(memory.created_at)}）"
        )
    return "\n".join(lines)


def render_pending_promises(promises: Sequence[PromiseRef]) -> str:
    if not promises:
        return "（なし）"
    lines = []
    for promise in promises:
        due = "期日なし" if promise.due_at is None else f"期日 {_date_label(promise.due_at)}（{promise.due_precision}）"
        lines.append(f"{promise.ref}: {one_line(promise.content)} / {due} / {promise.status}")
    return "\n".join(lines)


def render_turns(turns: Sequence[TurnRecord], character_name: str) -> str:
    blocks = []
    for index, turn in enumerate(turns, start=1):
        user_text = one_line(turn.user_text)[:TURN_TEXT_MAX_CHARS]
        reply_text = one_line(turn.reply_text)[:TURN_TEXT_MAX_CHARS]
        blocks.append(
            f"[{index}] {format_datetime_ja(turn.occurred_at)}\nユーザー: {user_text}\n{character_name}: {reply_text}"
        )
    return "\n\n".join(blocks)


class AnalysisPrompt:
    """memory_analysis.ja.txt（起動時に読み込み、プレースホルダを検証する）。"""

    def __init__(self, template: PromptTemplate) -> None:
        found = template.placeholders
        missing = ANALYSIS_REQUIRED - found
        unknown = found - ANALYSIS_REQUIRED - ANALYSIS_OPTIONAL
        if missing or unknown:
            raise PromptTemplateError(
                f"{ANALYSIS_TEMPLATE}: プレースホルダが不正です（不足: {sorted(missing)} / 未知: {sorted(unknown)}）"
            )
        self._template = template

    @classmethod
    def load(cls, prompts_dir: Path) -> AnalysisPrompt:
        path = prompts_dir / f"{ANALYSIS_TEMPLATE}.ja.txt"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise PromptTemplateError(f"{path}: 読み込めません: {exc}") from exc
        return cls(parse_template(ANALYSIS_TEMPLATE, text))

    def render(self, data: AnalysisInput) -> list[ChatMessage]:
        persona = data.persona
        focus = "\n".join(f"- {f}" for f in persona.memory_focus) or "（特になし）"
        values = {
            "name": persona.name,
            "first_person": persona.speech.first_person,
            "now": format_datetime_ja(data.now),
            "date_reference": date_reference(data.now),
            "threshold": f"{data.threshold:.2f}".rstrip("0").rstrip("."),
            "memory_focus": focus,
            "existing_memories": render_existing_memories(data.memories),
            "pending_promises": render_pending_promises(data.promises),
            "turns": render_turns(data.turns, persona.name),
        }
        return self._template.render(values)


def mock_context(data: AnalysisInput) -> dict[str, Any]:
    """MockLLM（engine/memory/mock.py）に渡す構造化データ。live では使われない。"""
    persona = data.persona
    return {
        "now": data.now.isoformat(),
        "threshold": data.threshold,
        "persona": {
            "name": persona.name,
            "first_person": persona.speech.first_person,
            "second_person": persona.speech.second_person,
        },
        "turns": [
            {"index": i, "user": t.user_text, "reply": t.reply_text, "at": t.occurred_at.isoformat()}
            for i, t in enumerate(data.turns, start=1)
        ],
        "memories": [
            {"ref": m.ref, "kind": m.kind, "content": m.content, "user_edited": m.is_user_edited} for m in data.memories
        ],
        "promises": [
            {
                "ref": p.ref,
                "content": p.content,
                "due_date": p.due_at.astimezone(JST).date().isoformat() if p.due_at is not None else None,
                "status": p.status,
            }
            for p in data.promises
        ],
    }
