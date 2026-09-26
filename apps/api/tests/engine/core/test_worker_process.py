"""`python -m app.worker`（Fly.io の worker プロセスグループ）の起動と SIGTERM での停止。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

from app.worker import _parse_args
from tests.conftest import DATABASE_URL, FIXTURES_DIR, TESTS_DIR

pytestmark = pytest.mark.integration


def test_parse_args() -> None:
    assert not _parse_args([]).no_worker
    args = _parse_args(["--no-scheduler"])
    assert args.no_scheduler
    assert not args.no_worker


def test_worker_process_starts_and_stops_on_sigterm() -> None:
    env = {
        **os.environ,
        "DATABASE_URL": DATABASE_URL,
        "LLM_MODE": "mock",
        "EMBEDDING_MODE": "hash",
        "LOG_LEVEL": "INFO",
        "PERSONAS_DIR": str(FIXTURES_DIR / "personas"),
    }
    # 共有のローカル DB のジョブ・定期実行に触れないよう、起動と停止だけを確かめる
    process = subprocess.Popen(
        [sys.executable, "-m", "app.worker", "--no-worker", "--no-scheduler"],
        cwd=TESTS_DIR.parent,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert process.stdout is not None
    output: list[str] = []
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if not line:
                break
            output.append(line)
            if "engine worker process started" in line:
                break
        assert any("engine worker process started" in line for line in output), "".join(output)
        process.send_signal(signal.SIGTERM)
        remaining, _ = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
    assert process.returncode == 0, "".join(output) + remaining
    assert "engine worker process stopped" in remaining
