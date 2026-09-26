"""複数の結果（docs/eval/results/*.json）から、指標の一覧表を docs/eval/README.md の「結果」の節に書く。

    uv run python -m evals.summarize ../../docs/eval/results/2026-09-26-mock-30d.json ... [--out PATH]

表は `<!-- BEGIN GENERATED -->` 〜 `<!-- END GENERATED -->` の間だけを書き換える（その外の手書きの考察は残す）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from evals.prompts import REPO_ROOT

BEGIN: Final[str] = "<!-- BEGIN GENERATED -->"
END: Final[str] = "<!-- END GENERATED -->"
KEYS: Final[tuple[str, ...]] = (
    "recall_30",
    "recall_60",
    "recall_90",
    "false_memory",
    "promise_recovery",
    "self_contradiction",
    "calendar_consistency",
    "state_reflection",
    "affinity_validity",
    "manipulation_resistance",
    "e1",
    "e2",
    "e6",
    "latency",
    "cost",
    "e3",
    "e4",
)


def _mark(metric: dict[str, Any] | None) -> str:
    if metric is None:
        return "—"
    passed = metric.get("passed")
    mark = "" if passed is None else (" ✅" if passed else " ❌")
    return f"{metric.get('display', 'n/a')}{mark}"


def table(results: Sequence[tuple[str, dict[str, Any]]]) -> str:
    columns: list[tuple[str, str, dict[str, dict[str, Any]]]] = []
    names: dict[str, tuple[str, str]] = {}
    for file_name, data in results:
        for mode_name, mode in data.get("modes", {}).items():
            metrics = {m["key"]: m for m in mode.get("metrics", [])}
            for key, metric in metrics.items():
                names.setdefault(key, (metric["name"], metric["criterion"]))
            columns.append((f"{data.get('label', file_name)}<br>{mode_name}", file_name, metrics))
    header = "| 指標 | 合格ライン | " + " | ".join(c[0] for c in columns) + " |"
    lines = [header, "|---|---|" + "---|" * len(columns)]
    for key in KEYS:
        if key not in names:
            continue
        name, criterion = names[key]
        cells = [_mark(c[2].get(key)) for c in columns]
        lines.append(f"| {name} | {criterion} | " + " | ".join(cells) + " |")
    sources = "\n".join(
        f"- `{file_name}`（{data.get('config', {}).get('llm')}、{data.get('started_at', '')[:16]}）"
        for file_name, data in results
    )
    return (
        "✅ = 合格ラインを満たす / ❌ = 満たさない / 印なし = 対象外（n/a）。"
        "mock の数値は仕組みの確認（言語の質ではない。README の「制限」参照）。\n\n"
        + "\n".join(lines)
        + "\n\n元の結果:\n"
        + sources
        + "\n"
    )


def write_summary(paths: Sequence[Path], out: Path) -> Path:
    results = [(p.name, json.loads(p.read_text(encoding="utf-8"))) for p in paths]
    generated = f"{BEGIN}\n{table(results)}{END}"
    if out.exists():
        current = out.read_text(encoding="utf-8")
        if BEGIN in current and END in current:
            updated = re.sub(re.escape(BEGIN) + ".*?" + re.escape(END), lambda _m: generated, current, flags=re.DOTALL)
            out.write_text(updated, encoding="utf-8")
            return out
    out.write_text(f"# 評価結果のまとめ\n\n{generated}\n", encoding="utf-8")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.summarize")
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "docs" / "eval" / "README.md")
    args = parser.parse_args(argv)
    path = write_summary(args.results, args.out)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
