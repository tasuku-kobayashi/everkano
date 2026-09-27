"""Fixtures: a mock ComfyUI server (HTTP + WS), a mock face engine and a TestClient with the lifespan running."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.face import MockFaceEngine
from app.main import create_app
from tools.mock_comfy import MockComfyServer

API_KEY = "test-api-key-0123456789"  # check-secrets: allow - dummy key for tests
HEADERS = {"X-API-Key": API_KEY}
REPO = Path(__file__).resolve().parents[2]  # portrait-studio


@pytest.fixture(scope="session")
def mock_comfy(tmp_path_factory: pytest.TempPathFactory) -> Iterator[MockComfyServer]:
    server = MockComfyServer(tmp_path_factory.mktemp("mock-comfy"))
    server.start()
    yield server
    server.stop()


@pytest.fixture
def settings(tmp_path: Path, mock_comfy: MockComfyServer) -> Settings:
    return Settings(
        api_key=API_KEY,
        comfy_url=mock_comfy.url,
        data_dir=tmp_path / "data",
        workflows_dir=REPO / "workflows",
        web_dist_dir=tmp_path / "no-dist",
        face_engine="mock",
        vram_table_path=Path(__file__).parent / "fixtures" / "vram_table.test.json",
        comfy_generation_timeout_seconds=60,
        comfy_poll_interval_seconds=0.05,
        log_level="WARNING",
    )


@pytest.fixture
def client(settings: Settings, mock_comfy: MockComfyServer) -> Iterator[TestClient]:
    mock_comfy.state.vram_free_mb = 11000
    mock_comfy.state.fail_next = None
    mock_comfy.state.missing_classes = set()
    mock_comfy.state.step_delay = 0.01
    app = create_app(settings, face_engine=MockFaceEngine())
    with TestClient(app) as c:
        c.headers.update(HEADERS)
        yield c


def wait_job(client: TestClient, job_id: str, *, timeout: float = 30.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error", "canceled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {client.get(f'/api/jobs/{job_id}').json()}")
