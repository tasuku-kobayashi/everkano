"""Write the OpenAPI document to ../docs/api/openapi.json (used by `openapi-typescript` for web/src/api/types.ts).

uv run python scripts/export_openapi.py            # write
uv run python scripts/export_openapi.py --check    # exit 1 when the file is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.main import create_app

OUT = Path(__file__).resolve().parents[2] / "docs" / "api" / "openapi.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    settings = Settings(api_key="openapi-export-dummy-key", _env_file=None)  # type: ignore[call-arg]
    spec = create_app(settings).openapi()
    text = json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT} is stale: run `uv run python scripts/export_openapi.py`", file=sys.stderr)
            return 1
        print("openapi.json is up to date")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT} ({len(spec['paths'])} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
