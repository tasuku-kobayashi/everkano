"""packages/personas/*.yaml を API と同じ Pydantic モデルで検証する（age >= 20、key = ファイル名 など）。

使い方（apps/api で）: uv run python scripts/validate_personas.py [ディレクトリ]
"""

from __future__ import annotations

import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from app.services.persona import PersonaLoadError, PersonaRepository  # noqa: E402


def main() -> int:
    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else API_ROOT.parents[1] / "packages" / "personas"
    try:
        repo = PersonaRepository.load_dir(directory)
    except PersonaLoadError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"OK: {len(repo)} personas in {directory}: {', '.join(repo.keys())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
