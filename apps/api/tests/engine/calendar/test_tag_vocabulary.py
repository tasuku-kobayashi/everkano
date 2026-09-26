"""画像タグの語彙（app/engine/types.py の TAG_VOCABULARY）を使う箇所がすべて語彙の中にあること。

- カレンダーの既定のタグ（行事・誕生日）
- ペルソナ YAML（packages/personas/*.yaml・テスト用ペルソナ）の post_tags
- ペルソナの検証スクリプト（packages/personas/scripts/engine_checks.py）が同じ語彙を使うこと
（画像プール seed_engine.sql とキャプションの説明は tests/engine/calendar/test_captions.py が検査する）
"""

from __future__ import annotations

import importlib.util
import sys

from app.engine.calendar.generator import BIRTHDAY_TAGS
from app.engine.calendar.life import SEASONAL_DEFAULT_TAGS
from app.engine.types import SEASONAL_KEYS, TAG_VOCABULARY
from app.services.persona import PersonaRepository
from tests.conftest import FIXTURES_DIR, REPO_ROOT

PERSONAS_DIR = REPO_ROOT / "packages" / "personas"


def test_calendar_default_tags_are_in_the_vocabulary() -> None:
    assert set(SEASONAL_DEFAULT_TAGS) == set(SEASONAL_KEYS)
    for key, tags in SEASONAL_DEFAULT_TAGS.items():
        assert tags, key
        assert set(tags) <= set(TAG_VOCABULARY), key
    assert set(BIRTHDAY_TAGS) <= set(TAG_VOCABULARY)


def test_persona_post_tags_are_in_the_vocabulary() -> None:
    for directory in (PERSONAS_DIR, FIXTURES_DIR / "personas"):
        repo = PersonaRepository.load_dir(directory)
        persona_keys = repo.keys()
        assert persona_keys
        for key in persona_keys:
            persona = repo.get(key)
            assert persona is not None
            if persona.engine is None:
                continue
            life = persona.engine.life
            tags = [
                *(t for b in life.routine for t in b.post_tags),
                *(t for e in life.events for t in e.post_tags),
                *(t for r in persona.engine.seasonal for t in r.post_tags),
            ]
            assert set(tags) <= set(TAG_VOCABULARY), (key, sorted(set(tags) - set(TAG_VOCABULARY)))


def test_persona_validator_uses_the_same_vocabulary() -> None:
    """packages/personas/scripts/engine_checks.py は語彙を写さず、API の TAG_VOCABULARY を使う。"""
    path = PERSONAS_DIR / "scripts" / "engine_checks.py"
    spec = importlib.util.spec_from_file_location("engine_checks_for_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        assert module.TAG_VOCABULARY is TAG_VOCABULARY
        errors: list[str] = []
        module._check_tags(["cafe", "not_a_tag"], 0.5, "test", errors)
        assert len(errors) == 1
        assert "not_a_tag" in errors[0]
    finally:
        sys.modules.pop(spec.name, None)
