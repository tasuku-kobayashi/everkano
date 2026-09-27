"""Scene presets (prompt fragments) and style parts. Built-ins ship in app/data; user edits live in SQLite."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.db import Database
from app.models import DEFAULT_NEGATIVE_PROMPT, DEFAULT_PREFIX_PROMPT, ScenePreset

DATA_DIR = Path(__file__).resolve().parent / "data"


def load_builtin_scenes() -> list[ScenePreset]:
    data = json.loads((DATA_DIR / "scene_presets.json").read_text(encoding="utf-8"))
    return [ScenePreset.from_dict({**item, "builtin": True}) for item in data]


def load_style_parts() -> dict[str, Any]:
    data = json.loads((DATA_DIR / "style_presets.json").read_text(encoding="utf-8"))
    return {
        "prefix_prompt": DEFAULT_PREFIX_PROMPT,
        "negative_prompt": DEFAULT_NEGATIVE_PROMPT,
        "parts": data.get("parts", []),
    }


class PresetService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._builtin = {p.id: p for p in load_builtin_scenes()}

    def list_scenes(self) -> list[ScenePreset]:
        merged: dict[str, ScenePreset] = dict(self._builtin)
        for override in self.db.list_scene_preset_overrides():
            base = merged.get(override.id)
            override.builtin = base.builtin if base else False
            override.verify = base.verify if base else override.verify
            merged[override.id] = override
        return list(merged.values())

    def get_scene(self, scene_id: str) -> ScenePreset | None:
        for preset in self.list_scenes():
            if preset.id == scene_id:
                return preset
        return None

    def upsert_scene(self, preset: ScenePreset) -> ScenePreset:
        base = self._builtin.get(preset.id)
        preset.builtin = base is not None
        preset.verify = base.verify if base else preset.verify
        self.db.upsert_scene_preset(preset)
        return preset

    def delete_scene(self, scene_id: str) -> bool:
        if scene_id in self._builtin:
            self.db.delete_scene_preset(scene_id)  # revert an edited built-in to its default
            return True
        existing = self.get_scene(scene_id)
        if existing is None:
            return False
        self.db.delete_scene_preset(scene_id)
        return True


def compose_prompt(prefix: str, fragments: list[str], user_prompt: str) -> str:
    parts = [prefix.strip()] + [f.strip() for f in fragments] + [user_prompt.strip()]
    return ", ".join(p for p in parts if p)
