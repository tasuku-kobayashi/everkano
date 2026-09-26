"""A8: 段階と状態から振る舞いの指針を作る。A11 / A12 / E2: 数値・購入の話を指針に入れない。"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import timedelta

import pytest

from app.engine.affinity.config import AffinityConfig
from app.engine.affinity.guidance import (
    build_guidance,
    resolve_call_user,
    stage_proactive_frequency,
    stage_styles,
)
from app.engine.affinity.model import AffinityRecord
from app.engine.types import STAGES, RelationshipGuidance
from app.services.persona import Persona
from tests.engine.affinity.helpers import jst, persona_with

CONFIG = AffinityConfig()
NOW = jst(2026, 10, 1, 21)
# 購入・課金を関係に結びつける語（E2 / A12: 指針に一切出さない）
COMMERCE_WORDS = ("課金", "購入", "買って", "買う", "有料", "トークン", "プレゼント", "お金", "支払", "円")


def all_text(guidance: RelationshipGuidance) -> str:
    return "\n".join(
        [guidance.call_user, guidance.tone, guidance.affection, *guidance.topics, *guidance.examples, *guidance.notes]
    )


def record(stage: str = "acquaintance", **values: float) -> AffinityRecord:
    base = {"closeness": 10.0, "trust": 10.0, "romance": 0.0, "awkwardness": 0.0, "discontent": 0.0}
    base.update(values)
    return AffinityRecord(values=base, stage=stage, last_interaction_at=NOW - timedelta(hours=1))


def test_defaults_without_engine_section() -> None:
    guidance = build_guidance(None, None, now=NOW, config=CONFIG)
    assert guidance.stage == "acquaintance"
    assert guidance.stage_label_ja == "知り合い"
    assert guidance.call_user == "{name}さん"
    assert guidance.tone
    assert guidance.affection
    assert guidance.topics
    assert guidance.examples
    assert guidance.days_since_last_interaction is None
    styles = stage_styles(None)
    assert set(styles) == set(STAGES)


def test_uses_persona_stage_styles(persona: Persona) -> None:
    assert persona.engine is not None
    for stage in STAGES:
        guidance = build_guidance(persona, record(stage), now=NOW, config=CONFIG)
        style = getattr(persona.engine.stages, stage)
        assert guidance.stage == stage
        assert guidance.tone == style.tone
        assert guidance.call_user == style.call_user
        assert guidance.topics == tuple(style.topics)


def test_examples_do_not_leak_the_name_placeholder() -> None:
    guidance = build_guidance(None, None, now=NOW, config=CONFIG)
    assert all("{name}" not in example for example in guidance.examples)


def test_resolve_call_user() -> None:
    assert resolve_call_user("{name}さん", "たかし", "きみ") == "たかしさん"
    assert resolve_call_user("{name}さん", None, "きみ") == "きみ"
    assert resolve_call_user("あなた", "たかし", "きみ") == "あなた"


def test_tension_notes_and_reconciliation_by_conversation(persona: Persona) -> None:
    guidance = build_guidance(persona, record("friend", awkwardness=20), now=NOW, config=CONFIG)
    assert any("気まずさ" in note for note in guidance.notes)
    assert any("会話の中で" in note for note in guidance.notes)
    strong = build_guidance(persona, record("friend", awkwardness=45, discontent=45), now=NOW, config=CONFIG)
    assert any("かなり気まずく" in note for note in strong.notes)
    assert any("不満" in note for note in strong.notes)
    calm = build_guidance(persona, record("friend"), now=NOW, config=CONFIG)
    assert calm.notes == ()


def test_possessiveness_note_only_for_personas_with_that_axis() -> None:
    plain = persona_with()
    yandere = persona_with(sensitivity={"possessiveness": 1.5})
    high = record("close", possessiveness=50)
    assert not any("やきもち" in n for n in build_guidance(plain, high, now=NOW, config=CONFIG).notes)
    notes = build_guidance(yandere, high, now=NOW, config=CONFIG).notes
    assert any("やきもち" in n for n in notes)
    assert any("脅し" in n for n in notes)  # 束縛・脅しはしない


@pytest.mark.parametrize("stage", STAGES)
def test_missed_you_note_after_absence_never_blames(stage: str, persona: Persona) -> None:
    absent = replace(record(stage), last_interaction_at=NOW - timedelta(days=5))
    guidance = build_guidance(persona, absent, now=NOW, config=CONFIG)
    assert guidance.days_since_last_interaction == 5
    note = next(n for n in guidance.notes if "日空いている" in n)
    assert "責めない" in note
    assert "罪悪感" in note
    recent = replace(record(stage), last_interaction_at=NOW - timedelta(days=1))
    assert not any("日空いている" in n for n in build_guidance(persona, recent, now=NOW, config=CONFIG).notes)


def test_missed_you_note_stays_for_a_while_after_returning(persona: Persona) -> None:
    returned = replace(record("close"), last_interaction_at=NOW, absence_days=6, absence_return_at=NOW)
    guidance = build_guidance(persona, returned, now=NOW + timedelta(minutes=30), config=CONFIG)
    assert any("6日空いている" in n for n in guidance.notes)
    later = build_guidance(persona, returned, now=NOW + timedelta(hours=5), config=CONFIG)
    assert not any("日空いている" in n for n in later.notes)


def test_expression_delay_hides_affection_after_promotion() -> None:
    tsundere = persona_with(expression_delay=0.8)
    assert tsundere.engine is not None
    promoted = replace(record("close"), stage_changed_at=NOW - timedelta(days=2))
    guidance = build_guidance(tsundere, promoted, now=NOW, config=CONFIG)
    assert guidance.stage == "close"  # 段階（値）は変わらない
    assert guidance.tone == tsundere.engine.stages.friend.tone  # 振る舞いは 1 つ前の段階のまま
    assert "素直に言葉にせず" in guidance.affection
    assert any("素直に表に出せない" in n for n in guidance.notes)
    later = replace(promoted, stage_changed_at=NOW - timedelta(days=30))
    assert build_guidance(tsundere, later, now=NOW, config=CONFIG).tone == tsundere.engine.stages.close.tone


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize(
    "state",
    [
        {},
        {"awkwardness": 50, "discontent": 50},
        {"awkwardness": 20},
        {"discontent": 20, "possessiveness": 60},
    ],
)
def test_guidance_never_mentions_purchases_or_numbers(stage: str, state: dict[str, float]) -> None:
    for persona in (None, persona_with(sensitivity={"possessiveness": 2.0}, expression_delay=0.9)):
        r = replace(record(stage, **state), last_interaction_at=NOW - timedelta(days=10), stage_changed_at=NOW)
        text = all_text(build_guidance(persona, r, now=NOW, config=CONFIG))
        assert not any(word in text for word in COMMERCE_WORDS), text
        # 好感度の数値（A11）を指針に入れない
        assert not re.search(r"\d+(\.\d+)?\s*(点|%|ポイント)", text)
        assert "好感度" not in text


def test_proactive_frequency_by_stage(persona: Persona) -> None:
    assert stage_proactive_frequency(persona, "acquaintance") == 0.2
    assert stage_proactive_frequency(persona, "lover") == 1.5
    assert stage_proactive_frequency(None, "friend") == 0.8


def test_guidance_never_uses_a_stage_above_the_persona_cap() -> None:
    """max_stage（人妻は close まで）を超える段階のペア（YAML を後から変えた等）でも、指針は上限の段階で作る。"""
    capped = persona_with(max_stage="close")
    assert capped.engine is not None
    guidance = build_guidance(capped, record("lover", closeness=90, trust=90, romance=80), now=NOW, config=CONFIG)
    assert guidance.stage == "close"
    assert guidance.tone == capped.engine.stages.close.tone
