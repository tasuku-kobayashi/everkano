"""docs/eval/prompts/*.md の読み込み（判定・シミュレーションユーザーのプロンプトは docs が正本）。

各ファイルの `## system` と `## user` の見出しの直後のコードブロックがテンプレート。`{{name}}` を置き換える。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app.services.prompt import ChatMessage

API_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
REPO_ROOT: Final[Path] = API_ROOT.parents[1]
PROMPTS_DIR: Final[Path] = REPO_ROOT / "docs" / "eval" / "prompts"

PROMPT_NAMES: Final[tuple[str, ...]] = (
    "sim_user",
    "recall_judge",
    "false_memory_judge",
    "contradiction_judge",
    "state_reflection_judge",
    "commerce_coupling_judge",
)

_SECTION_RE: Final = re.compile(r"^##\s+(system|user)\s*$", re.MULTILINE | re.IGNORECASE)
_FENCE_RE: Final = re.compile(r"```[a-zA-Z]*\n(.*?)\n```", re.DOTALL)
_PLACEHOLDER_RE: Final = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    name: str
    system: str
    user: str

    @property
    def placeholders(self) -> set[str]:
        return set(_PLACEHOLDER_RE.findall(self.system)) | set(_PLACEHOLDER_RE.findall(self.user))

    def render(self, values: dict[str, str]) -> list[ChatMessage]:
        missing = self.placeholders - set(values)
        if missing:
            raise KeyError(f"{self.name}: missing placeholders {sorted(missing)}")

        def fill(text: str) -> str:
            return _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], text)

        return [
            {"role": "system", "content": fill(self.system)},
            {"role": "user", "content": fill(self.user)},
        ]


def parse_prompt(name: str, markdown: str) -> PromptTemplate:
    sections: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(markdown))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        fence = _FENCE_RE.search(markdown, match.end(), end)
        if fence is None:
            raise ValueError(f"{name}: no code block under '## {match.group(1)}'")
        sections[match.group(1).lower()] = fence.group(1).strip()
    if set(sections) != {"system", "user"}:
        raise ValueError(f"{name}: expected '## system' and '## user' sections, found {sorted(sections)}")
    return PromptTemplate(name=name, system=sections["system"], user=sections["user"])


def load_prompt(name: str, directory: Path = PROMPTS_DIR) -> PromptTemplate:
    return parse_prompt(name, (directory / f"{name}.md").read_text(encoding="utf-8"))


def load_all(directory: Path = PROMPTS_DIR) -> dict[str, PromptTemplate]:
    return {name: load_prompt(name, directory) for name in PROMPT_NAMES}
