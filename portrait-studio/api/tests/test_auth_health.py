from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.face import MockFaceEngine
from app.main import create_app
from tests.conftest import API_KEY, REPO


def test_missing_api_key_refuses_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr("app.config.ENV_FILE", tmp_path / "nonexistent.env")
    with pytest.raises(Exception, match="api_key"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_endpoints_require_api_key(client: TestClient) -> None:
    client.headers.pop("X-API-Key")
    assert client.get("/api/characters").status_code == 401  # acceptance #4
    assert client.get("/api/images").status_code == 401
    assert client.post("/api/generate", json={}).status_code == 401
    assert client.get("/api/characters", headers={"X-API-Key": "wrong-key-value"}).status_code == 401
    assert client.get("/api/characters", headers={"X-API-Key": API_KEY}).status_code == 200


def test_health_is_public_and_reports_comfy(client: TestClient) -> None:
    client.headers.pop("X-API-Key")
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["comfy_connected"] is True
    assert body["vram_total_mb"] == 12282
    assert body["checkpoints"] == ["mock_photoreal_xl_v1.safetensors", "mock_photoreal_xl_v2.safetensors"]
    assert body["face_engine"] == "mock"
    assert all(w["ok"] for w in body["workflows"]), body["workflows"]


def test_health_503_when_comfy_unreachable(settings: Settings) -> None:
    down = settings.model_copy(update={"comfy_url": "http://127.0.0.1:9"})
    with TestClient(create_app(down, face_engine=MockFaceEngine())) as c:
        r = c.get("/api/health")
        assert r.status_code == 503
        assert r.json()["comfy_connected"] is False
        assert "接続できません" in r.json()["error"]
        # other endpoints that need ComfyUI report 503 with a detail, not a crash
        r2 = c.post("/api/generate/preview-vram", headers={"X-API-Key": API_KEY}, json={"method": "pulid"})
        assert r2.status_code == 503
        assert "接続できません" in r2.json()["detail"]


def test_workflow_missing_title_fails_startup(settings: Settings, tmp_path: Path) -> None:
    wf_dir = tmp_path / "wf"
    wf_dir.mkdir()
    for f in (REPO / "workflows").glob("*_api.json"):
        (wf_dir / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    broken = wf_dir / "portrait_pulid_api.json"
    broken.write_text(
        broken.read_text(encoding="utf-8").replace('"title": "FACE_APPLY"', '"title": "SOMETHING_ELSE"'),
        encoding="utf-8",
    )
    app = create_app(settings.model_copy(update={"workflows_dir": wf_dir}), face_engine=MockFaceEngine())
    with pytest.raises(Exception, match="FACE_APPLY"), TestClient(app):
        pass


def test_health_reports_missing_node_class(client: TestClient, mock_comfy) -> None:  # type: ignore[no-untyped-def]
    mock_comfy.state.missing_classes = {"ApplyInstantID"}
    client.app.state.services.workflow_checked = False  # type: ignore[attr-defined]
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert "instantid" not in body["face_methods"]
    instantid = next(w for w in body["workflows"] if w["method"] == "instantid")
    assert instantid["ok"] is False
    assert any("ApplyInstantID" in issue for issue in instantid["issues"])
