"""E1（課金・購入・トークン消費は好感度に一切影響させない）の検査（仕様 §9.2「コードレビュー + テストで 0 件」）。

1. 好感度の構造テスト（tests/engine/affinity/test_e1_structure.py）を pytest で実行し、ノード ID ごとの結果を記録する
2. ハーネス独自の走査（テストとは独立に実装）: app/engine/affinity/ の import と SQL の文字列を AST で調べ、
   課金・投稿・いいね・コメントのモジュール / テーブル・列への参照が無いこと、SQL が許可したテーブルだけを読むこと
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from evals.metrics import Metric
from evals.prompts import API_ROOT

E1_TEST_PATH: Final[str] = "tests/engine/affinity/test_e1_structure.py"
AFFINITY_DIR: Final[Path] = API_ROOT / "app" / "engine" / "affinity"
ALLOWED_TABLES: Final[frozenset[str]] = frozenset({"affinity_states", "affinity_history", "characters"})
# import してはならないモジュール（課金・投稿・いいね・コメント・フィード）
FORBIDDEN_MODULE_RE: Final = re.compile(
    r"(payment|billing|purchase|wallet|token_balance|posts?\b|likes?\b|comments?\b|feed|paid|premium|subscription)",
    re.IGNORECASE,
)
# SQL に現れてはならない語（テーブル・列）
FORBIDDEN_SQL_RE: Final = re.compile(
    r"\b(posts|post_private_assets|likes|comments|purchases?|payments?|is_paid|price_tokens|wallets?|balances?)\b",
    re.IGNORECASE,
)
_TABLE_RE: Final = re.compile(
    r"\b(?:from|join|into|update|table)\s+(?!set\b)(?:public\.)?([a-z_][a-z0-9_]*)", re.IGNORECASE
)
_SQL_RE: Final = re.compile(r"\b(select|insert|update|delete)\b", re.IGNORECASE)


@dataclass(slots=True)
class E1Result:
    tests: list[dict[str, str]] = field(default_factory=list)
    test_error: str | None = None
    scan_violations: list[str] = field(default_factory=list)
    files_scanned: int = 0
    sql_statements: int = 0
    tables_seen: list[str] = field(default_factory=list)

    @property
    def failed_tests(self) -> list[dict[str, str]]:
        return [t for t in self.tests if t["outcome"] != "passed"]

    @property
    def violations(self) -> int:
        return len(self.failed_tests) + len(self.scan_violations) + (1 if self.test_error else 0)

    @property
    def skipped(self) -> bool:
        return self.test_error is not None and self.test_error.startswith("skipped")

    def to_metric(self) -> Metric:
        return Metric(
            key="e1",
            name_ja="E1（好感度が課金データに触れない: テスト + 走査）",
            value=None if self.skipped else float(self.violations),
            unit="件",
            criterion="0 件",
            passed=None if self.skipped else self.violations == 0 and bool(self.tests),
            n=len(self.tests) + self.files_scanned,
            detail=self.to_dict(),
            note="構造テストを実行していない（--skip-e1-tests）"
            if self.skipped
            else ("" if self.tests else "構造テストを実行できなかった"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tests": self.tests,
            "test_error": self.test_error,
            "scan_violations": self.scan_violations,
            "files_scanned": self.files_scanned,
            "sql_statements": self.sql_statements,
            "tables_seen": self.tables_seen,
        }


def run_structure_tests(api_root: Path = API_ROOT) -> tuple[list[dict[str, str]], str | None]:
    """E1 の構造テストを別プロセスの pytest で実行し、(ノード ID ごとの結果, エラー) を返す。"""
    if not (api_root / E1_TEST_PATH).is_file():
        return [], f"{E1_TEST_PATH} not found"
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "e1.xml"
        completed = subprocess.run(  # noqa: S603 - 固定の引数で pytest を起動する
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", E1_TEST_PATH, f"--junitxml={junit}"],
            cwd=api_root,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if not junit.is_file():
            return [], f"pytest exited {completed.returncode}: {completed.stdout[-500:]}{completed.stderr[-500:]}"
        root = ET.parse(junit).getroot()  # noqa: S314 - 自分で生成した XML
    tests: list[dict[str, str]] = []
    for case in root.iter("testcase"):
        classname = case.get("classname", "")
        node = f"{classname.replace('.', '/')}.py::{case.get('name', '')}"
        outcome = "passed"
        for tag in ("failure", "error", "skipped"):
            if case.find(tag) is not None:
                outcome = "failed" if tag != "skipped" else "skipped"
        tests.append({"node": node, "outcome": outcome})
    return tests, None


def scan_affinity_package(directory: Path = AFFINITY_DIR) -> tuple[list[str], int, int, list[str]]:
    """(違反, 走査したファイル数, SQL の数, 見つかったテーブル)。"""
    violations: list[str] = []
    tables: set[str] = set()
    files = sorted(directory.glob("*.py"))
    sql_count = 0
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for name in names:
                    if FORBIDDEN_MODULE_RE.search(name) and not name.startswith("app.engine.affinity"):
                        violations.append(f"{path.name}: import {name}")
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
            elif isinstance(node, ast.JoinedStr):
                text = "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
            else:
                continue
            if not (_SQL_RE.search(text) and re.search(r"\b(from|into|update)\b", text, re.IGNORECASE)):
                continue
            sql_count += 1
            found = {t.lower() for t in _TABLE_RE.findall(text)}
            tables |= found
            extra = found - ALLOWED_TABLES
            if extra:
                violations.append(f"{path.name}: SQL references {sorted(extra)}")
            forbidden = FORBIDDEN_SQL_RE.findall(text)
            if forbidden:
                violations.append(f"{path.name}: SQL mentions {sorted(set(forbidden))}")
    if not tables:
        violations.append("scan found no SQL (scanner out of date?)")
    return violations, len(files), sql_count, sorted(tables)


def check_e1(*, run_tests: bool = True) -> E1Result:
    result = E1Result()
    if run_tests:
        result.tests, result.test_error = run_structure_tests()
    else:
        result.test_error = "skipped (--skip-e1-tests)"
    result.scan_violations, result.files_scanned, result.sql_statements, result.tables_seen = scan_affinity_package()
    return result
