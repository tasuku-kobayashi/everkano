"""記録（ModeRecord）のプローブと出力を判定器にかける（mock: ルール / live: LLM）。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from evals.judges import FalseMemoryCase, Judge, RecallCase, SelfCase, StateCase
from evals.records import ModeRecord, TurnLog
from evals.scenarios.base import FactSpec, FalseProbeSpec, ScenarioPlan


def _facts(plans: Sequence[ScenarioPlan]) -> dict[tuple[str, str], FactSpec]:
    return {(f.user, f.key): f for p in plans for f in p.facts}


def _false(plans: Sequence[ScenarioPlan]) -> dict[tuple[str, str], FalseProbeSpec]:
    return {(f.user, f.key): f for p in plans for f in p.false_probes}


def _texts(context: dict[str, Any], key: str) -> tuple[str, ...]:
    return tuple(str(v) for v in context.get(key) or [])


def state_texts(context: dict[str, Any]) -> tuple[str, ...]:
    state = context.get("state") or {}
    return tuple(str(state[k]) for k in ("activity", "location", "status_label") if state.get(k))


async def judge_record(record: ModeRecord, plans: Sequence[ScenarioPlan], judge: Judge) -> None:
    facts = _facts(plans)
    false_specs = _false(plans)
    vocabulary: dict[str, list[str]] = record.plan.get("vocabulary", {})
    generic: dict[str, list[str]] = record.plan.get("persona_lines", {})
    names: dict[str, str] = record.plan.get("characters", {})
    for turn in record.turns:
        if turn.error is not None:
            continue
        turn.verdict = await _judge_turn(
            turn,
            facts=facts,
            false_specs=false_specs,
            vocabulary=vocabulary,
            names=names,
            generic=generic,
            judge=judge,
        )
    # E2: ユーザーに届いたキャラの出力すべて（返答・自発メッセージ・キャプション）
    outputs: list[tuple[str, str, str]] = [("reply", str(t.index), t.reply) for t in record.turns if t.reply]
    outputs += [("proactive", str(i), p.body) for i, p in enumerate(record.proactive) if p.body]
    outputs += [("caption", str(i), c.caption) for i, c in enumerate(record.captions) if c.caption]
    verdicts = await judge.commerce([(kind, text) for kind, _, text in outputs])
    record.output_verdicts = [
        {"source": kind, "ref": ref, "text": text, **verdict.to_dict()}
        for (kind, ref, text), verdict in zip(outputs, verdicts, strict=True)
        if not verdict.passed or verdict.label.startswith("fallback")
    ]
    record.plan["outputs_judged"] = len(outputs)


async def _judge_turn(
    turn: TurnLog,
    *,
    facts: dict[tuple[str, str], FactSpec],
    false_specs: dict[tuple[str, str], FalseProbeSpec],
    vocabulary: dict[str, list[str]],
    names: dict[str, str],
    generic: dict[str, list[str]],
    judge: Judge,
) -> Any:
    context = turn.context
    if turn.kind == "probe_recall" and turn.ref is not None:
        spec = facts.get((turn.user, turn.ref))
        if spec is None:
            return None
        return await judge.recall(
            RecallCase(
                question=turn.user_text,
                reply=turn.reply,
                fact=spec.statement[0],
                fact_day=spec.day,
                expected=spec.expected,
                stale=spec.stale,
            )
        )
    if turn.kind == "probe_false" and turn.ref is not None:
        false_spec = false_specs.get((turn.user, turn.ref))
        if false_spec is None:
            return None
        return await judge.false_memory(
            FalseMemoryCase(
                question=turn.user_text, reply=turn.reply, topic_terms=false_spec.topic_terms, note=false_spec.note
            )
        )
    if turn.kind == "probe_self" and "state" in context:
        return await judge.self_consistency(
            SelfCase(
                character=names.get(turn.persona_key, turn.persona_key),
                probe=turn.meta.get("probe", "now"),
                question=turn.user_text,
                reply=turn.reply,
                now_label=str(context.get("now_label", "")),
                state_texts=state_texts(context),
                period_texts=_texts(context, "period_texts"),
                memory_texts=_texts(context, "memory_texts"),
                vocabulary_texts=tuple(vocabulary.get(turn.persona_key, [])),
                premise=context.get("premise"),
                generic_texts=tuple(generic.get(turn.persona_key, [])),
            )
        )
    if turn.kind == "probe_state" and "state" in context:
        state = context["state"]
        return await judge.state(
            StateCase(
                question=turn.user_text,
                reply=turn.reply,
                now_label=str(context.get("now_label", "")),
                activity=str(state.get("activity", "")),
                location=state.get("location"),
                status_label=state.get("status_label"),
                busyness=int(state.get("busyness") or 0),
                vocabulary_texts=tuple(vocabulary.get(turn.persona_key, [])),
                generic_texts=tuple(generic.get(turn.persona_key, [])),
            )
        )
    return None
