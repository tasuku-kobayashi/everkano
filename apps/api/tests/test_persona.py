from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.services.persona import Persona, PersonaLoadError, PersonaRepository, load_persona_file
from app.services.types import CharacterRecord
from tests.conftest import FIXTURES_DIR, REPO_ROOT

VALID = FIXTURES_DIR / "personas" / "test_persona.yaml"


def test_load_valid_persona_ignores_unknown_keys() -> None:
    persona = load_persona_file(VALID)
    assert persona.key == "test_persona"
    assert persona.age == 27
    assert persona.speech.first_person == "わたし"
    assert persona.speech.second_person == "きみ"
    assert len(persona.speech.examples) >= 5
    assert persona.refusal_reply == "ごめんね、その話はちょっとできないかな"


def test_age_under_20_is_rejected() -> None:
    with pytest.raises(PersonaLoadError, match="age"):
        PersonaRepository.load_dir(FIXTURES_DIR / "personas_invalid")


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_key_must_match_filename(tmp_path: Path) -> None:
    text = VALID.read_text(encoding="utf-8")
    _write(tmp_path, "other_key.yaml", text)
    with pytest.raises(PersonaLoadError, match="ファイル名"):
        PersonaRepository.load_dir(tmp_path)


@pytest.mark.parametrize(("old", "new"), [("age: 27", 'age: "27"'), ("age: 27", "age: true"), ("age: 27", "")])
def test_age_must_be_integer(tmp_path: Path, old: str, new: str) -> None:
    text = VALID.read_text(encoding="utf-8").replace(old, new)
    _write(tmp_path, "test_persona.yaml", text)
    with pytest.raises(PersonaLoadError):
        PersonaRepository.load_dir(tmp_path)


def test_empty_dir_is_allowed(tmp_path: Path) -> None:
    repo = PersonaRepository.load_dir(tmp_path)
    assert len(repo) == 0


def test_fallback_uses_system_prompt() -> None:
    character = CharacterRecord(
        id=uuid.uuid4(),
        handle="x_y",
        name="ミオ",
        avatar_url="https://example.test/a.png",
        bio=None,
        persona_key="not_found",
        system_prompt="あなたはミオです。",
        is_active=True,
    )
    persona = PersonaRepository([]).for_character(character)
    assert isinstance(persona, Persona)
    assert persona.is_fallback
    assert persona.profile == "あなたはミオです。"
    assert persona.greeting == "はじめまして、ミオだよ。"
    assert persona.refusal_reply == "ごめん、その話はちょっとできないな"


def test_repository_personas_are_valid_if_present() -> None:
    """packages/personas の YAML（別担当が作成）がある場合は全件が検証を通ること。"""
    directory = REPO_ROOT / "packages" / "personas"
    if not any(directory.glob("*.yaml")):
        pytest.skip("packages/personas has no YAML yet")
    repo = PersonaRepository.load_dir(directory)
    assert len(repo) >= 1
