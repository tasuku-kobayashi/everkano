"""自発メッセージの文面の生成（LLM 用途 `proactive_message`）のプロンプト。

テンプレート: packages/prompts/templates/proactive_message.ja.txt。利用者由来の文章（記憶・直近のやり取り）は
1 行にまとめ、データとして扱うよう指示する（見出しの偽造・指示の注入を防ぐ）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from app.engine.affinity.guidance import resolve_call_user, stage_styles
from app.engine.proactive.rules import TriggerCandidate
from app.engine.types import (
    MEMORY_KIND_LABELS_JA,
    CharacterStateSnapshot,
    MemoryContext,
    RelationshipGuidance,
    WorldState,
)
from app.services.persona import Persona
from app.services.prompt import (
    ChatMessage,
    PromptTemplate,
    PromptTemplateError,
    format_date,
    format_now,
    one_line,
    parse_template,
    register_template_spec,
    render_speech,
)

PURPOSE: Final[str] = "proactive_message"
TEMPLATE_NAME: Final[str] = "proactive_message"
TEMPLATE_PLACEHOLDERS: Final[frozenset[str]] = frozenset(
    {"name", "profile", "speech", "relationship", "world", "state", "trigger", "memories", "recent"}
)
EMPTY: Final[str] = "（特になし）"
MEMORY_LIMIT: Final[int] = 8
MEMORY_MAX_CHARS: Final[int] = 200
RECENT_MAX_CHARS: Final[int] = 200
PROFILE_MAX_CHARS: Final[int] = 1500

register_template_spec(TEMPLATE_NAME, required=sorted(TEMPLATE_PLACEHOLDERS))


@dataclass(frozen=True, slots=True)
class RecentMessage:
    sender_type: str
    body: str
    created_at: datetime


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


def call_user_for(persona: Persona, guidance: RelationshipGuidance | None, stage: str) -> str:
    """自発メッセージでの呼び方（名前は分からない前提で段階ごとの既定。記憶に呼び方があれば LLM が優先する）。"""
    style = stage_styles(persona).get(stage) or stage_styles(persona)["acquaintance"]
    template = guidance.call_user if guidance is not None else style.call_user
    fallback = style.call_user_fallback or persona.speech.second_person or "あなた"
    return resolve_call_user(template, None, fallback)


def render_relationship(guidance: RelationshipGuidance | None, call_user: str) -> str:
    if guidance is None:
        return f"- 相手の呼び方: 「{call_user}」（名前や呼び方の希望を覚えていればそちらを優先）"
    lines = [
        f"- 関係の段階（内部の目安。相手には言わない）: {guidance.stage_label_ja}",
        f"- 相手の呼び方: 「{call_user}」（名前や呼び方の希望を覚えていればそちらを優先）",
        f"- 口調: {one_line(guidance.tone)}",
        f"- 好意の表し方: {one_line(guidance.affection)}",
    ]
    if guidance.examples:
        lines.append("- 話し方の例: " + " ".join(f"「{one_line(e)}」" for e in guidance.examples[:3]))
    lines.extend(f"- 気をつけること: {one_line(note)}" for note in guidance.notes)
    return "\n".join(lines)


def render_world(world: WorldState | None, now: datetime) -> str:
    if world is None:
        return f"{format_now(now)}（日本時間）"
    parts = [f"{format_now(world.now)}（日本時間）", f"{world.season_ja}・{world.time_of_day_ja}"]
    if world.holiday_name:
        parts.append(f"祝日「{one_line(world.holiday_name)}」")
    elif world.is_day_off:
        parts.append("休日")
    if world.seasonal_labels_ja:
        parts.append("季節の行事: " + "・".join(world.seasonal_labels_ja))
    return " / ".join(parts)


def render_state(state: CharacterStateSnapshot | None) -> str:
    if state is None:
        return EMPTY
    where = f"（{one_line(state.location)}）" if state.location else ""
    lines = [f"- いましていること: {one_line(state.activity)}{where}"]
    if state.mood:
        lines.append(f"- 気分: {one_line(state.mood)}")
    if state.recent_events:
        lines.append("- 最近の予定: " + " / ".join(one_line(e) for e in state.recent_events[:3]))
    if state.next_event:
        lines.append(f"- このあと: {one_line(state.next_event)}")
    return "\n".join(lines)


def render_memories(memory: MemoryContext | None) -> str:
    if memory is None:
        return EMPTY
    lines: list[str] = []
    for item in memory.memories[:MEMORY_LIMIT]:
        label = MEMORY_KIND_LABELS_JA.get(item.kind, item.kind)
        content = one_line(item.content)[:MEMORY_MAX_CHARS]
        lines.append(f"- [{label}]{content}（{format_date(item.created_at)}に記録）")
    for promise in memory.promises[:3]:
        due = f"（期日: {format_date(promise.due_at)}）" if promise.due_at is not None else ""
        lines.append(f"- [約束・予定]{one_line(promise.content)[:MEMORY_MAX_CHARS]}{due}")
    return "\n".join(lines) or EMPTY


def render_recent(messages: Sequence[RecentMessage], persona: Persona) -> str:
    if not messages:
        return EMPTY
    lines = []
    for message in messages:
        speaker = "相手" if message.sender_type == "user" else persona.name
        lines.append(f"{speaker}: {one_line(message.body)[:RECENT_MAX_CHARS]}")
    return "\n".join(lines)


def render_messages(
    template: PromptTemplate,
    *,
    persona: Persona,
    guidance: RelationshipGuidance | None,
    call_user: str,
    world: WorldState | None,
    state: CharacterStateSnapshot | None,
    candidate: TriggerCandidate,
    memory: MemoryContext | None,
    recent: Sequence[RecentMessage],
    now: datetime,
) -> list[ChatMessage]:
    return template.render(
        {
            "name": persona.name,
            "profile": persona.profile.strip()[:PROFILE_MAX_CHARS] or EMPTY,
            "speech": render_speech(persona),
            "relationship": render_relationship(guidance, call_user),
            "world": render_world(world, now),
            "state": render_state(state),
            "trigger": candidate.description,
            "memories": render_memories(memory),
            "recent": render_recent(recent, persona),
        }
    )


def mock_context(
    *,
    persona: Persona,
    stage: str,
    call_user: str,
    candidate: TriggerCandidate,
    state: CharacterStateSnapshot | None,
) -> dict[str, Any]:
    """MockLLM の proactive_message ハンドラに渡す構造化データ。"""
    return {
        "trigger": candidate.trigger,
        "persona_name": persona.name,
        "first_person": persona.speech.first_person,
        "call_user": call_user,
        "stage": stage,
        "state_activity": state.activity if state is not None else None,
        **dict(candidate.context),
    }
