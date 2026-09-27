from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import wait_job
from tests.test_characters import create_character


def test_scene_presets_builtin_edit_and_revert(client: TestClient) -> None:
    items = client.get("/api/presets/scenes").json()["items"]
    ids = {i["id"] for i in items}
    for required in (
        "cafe",
        "beach",
        "japanese_room",
        "office",
        "night_city",
        "outdoor_portrait",
        "indoor_natural_light",
        "studio",
    ):
        assert required in ids
    assert {i["id"] for i in items if i["verify"]} == {"portrait_closeup", "upper_body_cafe", "full_body_street"}
    cafe = next(i for i in items if i["id"] == "cafe")
    assert cafe["builtin"] is True and isinstance(cafe["fragments"], list) and len(cafe["fragments"]) >= 2
    r = client.post("/api/presets/scenes", json={"id": "cafe", "name": "カフェ（改）", "fragments": ["at a tiny cafe"]})
    assert r.status_code == 200 and r.json()["builtin"] is True
    assert (
        next(i for i in client.get("/api/presets/scenes").json()["items"] if i["id"] == "cafe")["name"]
        == "カフェ（改）"
    )
    r = client.post("/api/presets/scenes", json={"id": "onsen", "name": "温泉", "fragments": ["at a hot spring"]})
    assert r.status_code == 200 and r.json()["builtin"] is False
    assert client.delete("/api/presets/scenes/cafe").status_code == 200  # revert builtin
    assert next(i for i in client.get("/api/presets/scenes").json()["items"] if i["id"] == "cafe")["name"] == "カフェ"
    assert client.delete("/api/presets/scenes/onsen").status_code == 200
    assert client.delete("/api/presets/scenes/onsen").status_code == 404
    assert client.post("/api/presets/scenes", json={"id": "Bad Id!", "name": "x"}).status_code == 422
    styles = client.get("/api/presets/styles").json()
    assert styles["prefix_prompt"].startswith("photorealistic") and any(
        p["category"] == "lighting" for p in styles["parts"]
    )


def test_system_endpoints(client: TestClient, mock_comfy) -> None:  # type: ignore[no-untyped-def]
    vram = client.get("/api/system/vram").json()
    assert vram["total_mb"] == 12282 and vram["free_mb"] == 11000 and vram["used_mb"] == 12282 - 11000
    mock_comfy.state.vram_free_mb = 3000
    freed = client.post("/api/system/free").json()
    assert freed["ok"] and freed["free_mb_before"] == 3000 and freed["free_mb_after"] > 3000
    models = client.get("/api/system/models").json()["folders"]
    assert models["checkpoints"] == ["mock_photoreal_xl_v1.safetensors", "mock_photoreal_xl_v2.safetensors"]
    assert "loras" in models
    table = client.get("/api/system/vram-table").json()
    assert table["entries"][0]["method"] == "txt2img" and table["gpu"] == "TEST FIXTURE (not a measurement)"


def test_shipped_vram_table_is_unmeasured_template() -> None:
    data = json.loads(
        (Path(__file__).resolve().parents[1] / "app" / "data" / "vram_table.json").read_text(encoding="utf-8")
    )
    assert data["measured_at"] is None
    assert all(e["peak_mb"] is None for e in data["entries"]), "vram_table.json must only contain real measurements"


def test_audit_log_one_line_per_generation(client: TestClient, settings) -> None:  # type: ignore[no-untyped-def]
    c = create_character(client, identity=70)
    wait_job(
        client,
        client.post(
            "/api/generate", json={"character_id": c["id"], "prompt": "p1", "count": 2, "adult_only": True, "seed": 5}
        ).json()["job_id"],
    )
    wait_job(client, client.post("/api/characters/draft", json={"prompt": "seed", "count": 1}).json()["job_id"])
    wait_job(
        client,
        client.post(
            f"/api/characters/{c['id']}/verify", json={"face_weights": [0.8], "scenes": ["portrait_closeup"]}
        ).json()["job_id"],
    )
    path = Path(settings.data_dir) / "logs" / "audit.jsonl"
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 3  # acceptance #17: one line per generation job
    gen = lines[0]
    for key in (
        "timestamp",
        "api_key_id",
        "endpoint",
        "character_id",
        "character_version",
        "prompt",
        "negative_prompt",
        "seed",
        "face_method",
        "face_weight",
        "checkpoint",
        "width",
        "height",
        "count",
        "similarity_scores",
        "output_files",
        "duration_ms",
    ):
        assert key in gen, key
    assert (
        gen["endpoint"] == "/api/generate"
        and gen["count"] == 2
        and len(gen["output_files"]) == 2
        and gen["status"] == "done"
    )
    assert lines[1]["endpoint"] == "/api/characters/draft" and lines[2]["endpoint"] == "/api/characters/{id}/verify"
    api = client.get("/api/audit", params={"limit": 2}).json()
    assert api["total_lines"] == 3 and len(api["items"]) == 2 and api["items"][0]["job_id"] == lines[2]["job_id"]
    assert api["path"].endswith("audit.jsonl")


def test_cookie_auth_compliance_and_workflow_json(client: TestClient, settings, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    c = create_character(client, identity=70)
    client.headers.pop("X-API-Key")
    assert client.get("/api/system/vram").status_code == 401
    client.cookies.set("psk", "test-api-key-0123456789")
    # the cookie is honoured by the <img src> routes only; every other route stays header-only (CSRF surface)
    assert client.get(c["references"][0]["file_url"]).status_code == 200
    assert client.get("/api/system/vram").status_code == 401
    assert client.get("/api/characters").status_code == 401
    assert client.delete(f"/api/characters/{c['id']}").status_code == 401
    client.headers["X-API-Key"] = "test-api-key-0123456789"
    r = client.get("/api/system/workflows/pulid")
    assert r.status_code == 200 and r.json()["titles"]["FACE_APPLY"] and "nodes" in r.json()
    assert client.get("/api/system/workflows/nope").status_code == 404
    r = client.get("/api/system/compliance")
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert "実在" in r.json()["markdown"]
