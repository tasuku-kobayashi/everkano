"""OpenAPI スキーマを docs/api/openapi.json に書き出す（キー順は安定化）。

使い方（apps/api で）: uv run python scripts/export_openapi.py [--check]
  --check: 書き出さずに差分があれば終了コード 1（CI 用）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
OUTPUT = REPO_ROOT / "docs" / "api" / "openapi.json"

sys.path.insert(0, str(API_ROOT))

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


def render() -> str:
    # .env や環境変数に依存しない既定設定でスキーマを生成する
    settings = Settings(_env_file=None, app_env="local", llm_mode="mock", embedding_mode="hash")
    schema = create_app(settings).openapi()
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    content = render()
    if "--check" in sys.argv:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != content:
            print(f"{OUTPUT} is out of date. Run: pnpm --filter @everkano/api openapi", file=sys.stderr)
            return 1
        print(f"{OUTPUT} is up to date")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(content, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
