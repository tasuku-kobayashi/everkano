#!/usr/bin/env python3
"""THIRD_PARTY_NOTICES.md（本番で使う依存パッケージのライセンス一覧）を生成する。

使い方（リポジトリのルートで。pnpm install と apps/api の uv sync が済んでいること）:
    uv run --frozen --project apps/api python scripts/generate-third-party-notices.py

- npm: `pnpm licenses list --prod --json`（Web の本番依存。devDependencies は含まない）
- Python: `uv export --frozen --no-dev`（API の本番依存）の各パッケージのメタデータ
  （License-Expression → License の classifier → License の順）。apps/api の仮想環境で実行すること
依存を更新したら再生成してコミットする。プラットフォーム固有のパッケージ（sharp のバイナリなど）は
実行した OS / CPU のものが載る（本番・CI と同じ linux-x64 で生成する）。
"""

from __future__ import annotations

import importlib.metadata as md
import json
import platform
import subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "THIRD_PARTY_NOTICES.md"

HEADER = """# Third-party notices（依存パッケージのライセンス）

everkano が本番で使うサードパーティのパッケージと、そのライセンスの一覧。
`scripts/generate-third-party-notices.py` で生成する（手で編集しない）。依存を更新したら再生成してコミットすること。

## 帰属表示・扱いに注意が必要なもの

| パッケージ | ライセンス | 扱い |
| ---------- | ---------- | ---- |
"""

# （パッケージ, ライセンス, 扱い）
NOTES: list[tuple[str, str, str]] = [
    (
        "`caniuse-lite`（npm。Next.js のビルドが使うブラウザ対応表）",
        "CC-BY-4.0",
        'データの出典の表示が必要: "Browser support data from caniuse.com (Alexis Deveria), CC BY 4.0"',
    ),
    (
        "`certifi`（Python。httpx の CA 証明書）",
        "MPL-2.0",
        "ファイル単位のコピーレフト。改変して配布する場合はそのファイルのソースを公開する（改変していない）",
    ),
    (
        "`@img/sharp-libvips-*`（npm。Next.js の任意依存 sharp のネイティブライブラリ）",
        "LGPL-3.0-or-later",
        "インストールはされるが、画像最適化を無効（`images.unoptimized: true`）にしているため実行時には読み込まれない。"
        "配布物に含める場合は LGPL の条件（差し替え可能な形での提供など）に従う",
    ),
    (
        "`@playwright/test` / `playwright` / `playwright-core`（npm）",
        "Apache-2.0",
        "Next.js の任意の peer 依存として本番依存の一覧に出るが、アプリのコードからは使っていない（E2E テスト用）",
    ),
]

FOOTER = """
上記以外は MIT / BSD / Apache-2.0 / ISC / PSF などの寛容なライセンス。各パッケージの著作権表示とライセンス本文は、
インストールされたパッケージ（`node_modules/<name>/LICENSE`、Python の `*.dist-info/`）に含まれる。
"""


def npm_packages() -> dict[str, list[tuple[str, str, str]]]:
    raw = subprocess.run(
        ["pnpm", "licenses", "list", "--prod", "--json"],  # noqa: S607 - 開発者の PATH の pnpm を使う
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    data: dict[str, list[dict[str, object]]] = json.loads(raw)
    by_license: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for license_name, packages in data.items():
        for package in packages:
            raw_versions = package.get("versions")
            versions = ", ".join(str(v) for v in raw_versions) if isinstance(raw_versions, list) else ""
            homepage = str(package.get("homepage") or "")
            by_license[license_name].append((str(package["name"]), versions, homepage))
    return by_license


def python_package_names() -> list[str]:
    exported = subprocess.run(
        ["uv", "export", "--frozen", "--no-dev", "--no-hashes", "--no-emit-project", "--format", "requirements-txt"],  # noqa: S607
        cwd=ROOT / "apps" / "api",
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    names = []
    for raw_line in exported.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        names.append(line.split("==", 1)[0].split(";", 1)[0].strip())
    return sorted(set(names), key=str.lower)


def python_license(meta: md.PackageMetadata) -> str:
    expression = meta.get("License-Expression")
    if expression:
        return str(expression)
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        return " / ".join(classifiers)
    raw = (meta.get("License") or "").strip()
    return raw.splitlines()[0][:80] if raw else "UNKNOWN"


def python_homepage(meta: md.PackageMetadata) -> str:
    homepage = meta.get("Home-page")
    if homepage:
        return str(homepage)
    for entry in meta.get_all("Project-URL") or []:
        key, _, value = str(entry).partition(",")
        if key.strip().lower() in {"homepage", "source", "repository", "source code", "documentation"}:
            return value.strip()
    return ""


def python_packages() -> list[tuple[str, str, str, str]]:
    rows = []
    for name in python_package_names():
        try:
            meta = md.metadata(name)
        except md.PackageNotFoundError as exc:
            raise SystemExit(f"error: {name} がインストールされていません（cd apps/api && uv sync）") from exc
        rows.append((str(meta["Name"]), str(meta["Version"]), python_license(meta), python_homepage(meta)))
    return rows


def main() -> None:
    lines = [HEADER.rstrip("\n")]
    lines += [f"| {package} | {license_name} | {note} |" for package, license_name, note in NOTES]
    lines.append(FOOTER)
    npm = npm_packages()
    lines.append(f"## Web（npm、`pnpm licenses list --prod`、{platform.system().lower()}-{platform.machine()}）\n")
    lines.append("| パッケージ | バージョン | ライセンス |")
    lines.append("| ---------- | ---------- | ---------- |")
    for license_name in sorted(npm):
        for name, versions, _homepage in sorted(npm[license_name]):
            lines.append(f"| `{name}` | {versions} | {license_name} |")
    lines.append("")
    lines.append("## API（Python、`uv export --no-dev`）\n")
    lines.append("| パッケージ | バージョン | ライセンス | URL |")
    lines.append("| ---------- | ---------- | ---------- | --- |")
    for name, version, license_name, homepage in python_packages():
        lines.append(f"| `{name}` | {version} | {license_name} | {homepage} |")
    lines.append("")
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
