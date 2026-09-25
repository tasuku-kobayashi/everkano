"""統合テストが DB に繋がらないとき、CI（REQUIRE_TEST_DB）では skip せずに失敗すること。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
UNREACHABLE = "postgresql://postgres:postgres@127.0.0.1:1/postgres"
TARGET = "tests/integration/test_comments_api.py::test_comment_rate_limit"


def _run(**env: str) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if k not in {"CI", "REQUIRE_TEST_DB", "TEST_DATABASE_URL"}}
    return subprocess.run(  # noqa: S603 - 固定の引数でテストランナーを起動する
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", TARGET],
        cwd=API_ROOT,
        env={**base, "TEST_DATABASE_URL": UNREACHABLE, **env},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_integration_tests_fail_instead_of_skipping_when_db_is_required() -> None:
    required = _run(REQUIRE_TEST_DB="1")
    assert required.returncode != 0, required.stdout
    assert "database not reachable" in required.stdout + required.stderr
    on_ci = _run(CI="true")
    assert on_ci.returncode != 0, on_ci.stdout


def test_local_runs_still_skip_without_db() -> None:
    local = _run()
    assert local.returncode == 0, local.stdout + local.stderr
    assert "1 skipped" in local.stdout
    opted_out = _run(CI="true", REQUIRE_TEST_DB="0")
    assert opted_out.returncode == 0, opted_out.stdout + opted_out.stderr
