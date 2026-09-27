from __future__ import annotations

import time

from fastapi.testclient import TestClient

from tests.conftest import wait_job
from tests.test_characters import create_character


def test_generate_requires_adult_only(client: TestClient) -> None:
    c = create_character(client)
    r = client.post("/api/generate", json={"character_id": c["id"], "prompt": "test", "count": 1})  # acceptance #5
    assert r.status_code == 400 and "adult_only" in r.json()["detail"]
    r = client.post("/api/generate", json={"character_id": c["id"], "prompt": "test", "count": 1, "adult_only": False})
    assert r.status_code == 400


def test_generate_protects_locked_params(client: TestClient) -> None:
    c = create_character(client)
    r = client.post(
        "/api/generate",
        json={
            "character_id": c["id"],
            "prompt": "x",
            "count": 1,
            "adult_only": True,
            "overrides": {"face_weight": 0.1},
        },
    )  # acceptance #6
    assert r.status_code == 400
    assert "この操作はキャラの同一性を変えます" in r.json()["detail"] and "face_weight" in r.json()["detail"]
    # same value as locked is not a change
    r = client.post(
        "/api/generate",
        json={
            "character_id": c["id"],
            "prompt": "x",
            "count": 1,
            "adult_only": True,
            "overrides": {"face_weight": 0.8},
        },
    )
    assert r.status_code == 202
    # explicit override is allowed and recorded, but NOT saved to the character
    r = client.post(
        "/api/generate",
        json={
            "character_id": c["id"],
            "prompt": "x",
            "count": 1,
            "adult_only": True,
            "overrides": {"face_weight": 1.0},
            "allow_locked_override": True,
        },
    )
    assert r.status_code == 202, r.text
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "done"
    img = client.get(f"/api/images/{job['result_image_ids'][0]}").json()
    assert img["params_snapshot"]["face_weight"] == 1.0 and img["params_snapshot"]["locked_override"] is True
    assert client.get(f"/api/characters/{c['id']}").json()["locked"]["face_weight"] == 0.8
    assert client.get(f"/api/characters/{c['id']}").json()["current_version"] == 1


def test_generate_save_as_version(client: TestClient) -> None:
    c = create_character(client)
    r = client.post(
        "/api/generate",
        json={
            "character_id": c["id"],
            "prompt": "x",
            "count": 1,
            "adult_only": True,
            "overrides": {"face_weight": 1.0},
            "allow_locked_override": True,
            "save_as_version": True,
        },
    )
    assert r.status_code == 202, r.text
    detail = client.get(f"/api/characters/{c['id']}").json()
    assert detail["current_version"] == 2 and detail["locked"]["face_weight"] == 1.0
    job = wait_job(client, r.json()["job_id"])
    assert client.get(f"/api/images/{job['result_image_ids'][0]}").json()["character_version"] == 2


def test_generate_rejects_dangerous_resolution(client: TestClient) -> None:
    c = create_character(client)
    r = client.post(
        "/api/generate",
        json={"character_id": c["id"], "prompt": "x", "count": 1, "adult_only": True, "width": 2048, "height": 2048},
    )  # acceptance #7
    assert r.status_code == 400
    assert "未実測" in r.json()["detail"] or "OOM" in r.json()["detail"]
    r = client.post(
        "/api/generate",
        json={"character_id": c["id"], "prompt": "x", "count": 1, "adult_only": True, "width": 1536, "height": 1536},
    )
    assert r.status_code == 400 and "OOMの可能性が高い" in r.json()["detail"] and "13500" in r.json()["detail"]
    r = client.post(
        "/api/generate", json={"character_id": c["id"], "prompt": "x", "count": 1, "adult_only": True, "upscale": 1.5}
    )
    assert r.status_code == 400 and "hires_max" in r.json()["detail"]
    assert (
        client.post(
            "/api/generate", json={"character_id": c["id"], "prompt": "x", "count": 9, "adult_only": True}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/generate", json={"character_id": c["id"], "prompt": "x", "count": 0, "adult_only": True}
        ).status_code
        == 422
    )


def test_preview_vram(client: TestClient, mock_comfy) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        "/api/generate/preview-vram", json={"method": "pulid", "width": 832, "height": 1216, "count": 4, "upscale": 1.3}
    )
    assert r.status_code == 200
    body = r.json()
    assert (
        body["estimated_peak_mb"] == 9800
        and body["free_mb"] == 11000
        and body["risk"] == "low"
        and body["basis"] == "measured"
    )
    mock_comfy.state.vram_free_mb = 4000
    body = client.post(
        "/api/generate/preview-vram", json={"method": "pulid", "width": 832, "height": 1216, "upscale": 1.3}
    ).json()
    assert body["risk"] == "medium" and "upscale を 1.0" in body["advice"] and body["would_reject"] is False
    body = client.post("/api/generate/preview-vram", json={"method": "pulid", "width": 1536, "height": 1536}).json()
    assert body["risk"] == "high" and body["would_reject"] is True and "OOM" in body["advice"]
    body = client.post("/api/generate/preview-vram", json={"method": "instantid", "width": 1024, "height": 1536}).json()
    assert body["risk"] == "unknown" and body["basis"] == "unknown" and body["would_reject"] is True


def test_generate_end_to_end_with_similarity_and_scenes(client: TestClient) -> None:
    c = create_character(client, identity=90)
    r = client.post(
        "/api/generate",
        json={
            "character_id": c["id"],
            "prompt": "standing in a cafe, casual outfit",
            "count": 4,
            "adult_only": True,
            "seed": 12345,
            "scene_ids": ["cafe"],
        },
    )  # acceptance #13
    assert r.status_code == 202 and r.json()["position"] == 0
    job_id = r.json()["job_id"]
    job = wait_job(client, job_id)  # acceptance #14
    assert job["status"] == "done" and job["progress"]["total_images"] == 4 and job["progress"]["current"] == 4
    assert job["progress"]["step"] > 0 and job["progress"]["total"] > 0  # step progress came through the websocket
    assert len(job["result_image_ids"]) == 4 and job["queue_position"] is None
    images = [client.get(f"/api/images/{i}").json() for i in job["result_image_ids"]]
    assert [img["seed"] for img in images] == [12345, 12346, 12347, 12348]
    for img in images:
        assert (
            img["similarity"] is not None and img["similarity"] > 0.99 and img["similarity_status"] == "computed"
        )  # acceptance #15/16
        snap = img["params_snapshot"]
        assert snap["scene_ids"] == ["cafe"] and "sitting at a cafe by the window" in snap["positive"]
        assert snap["positive"].endswith("standing in a cafe, casual outfit")
        assert snap["face_method"] == "pulid" and snap["face_weight"] == 0.8 and snap["face_detailer"] is True
        assert snap["width"] == 832 and snap["height"] == 1216 and snap["steps"] == 28
    assert client.get(f"/api/characters/{c['id']}").json()["stats"]["generations"] == 4
    listed = client.get("/api/images", params={"character_id": c["id"]}).json()
    assert listed["total"] == 4


def test_regenerate_reproduces_snapshot(client: TestClient) -> None:
    c = create_character(client, identity=91)
    job = wait_job(
        client,
        client.post(
            "/api/generate",
            json={
                "character_id": c["id"],
                "prompt": "beach",
                "count": 1,
                "adult_only": True,
                "seed": 999,
                "scene_ids": ["beach"],
            },
        ).json()["job_id"],
    )
    original = client.get(f"/api/images/{job['result_image_ids'][0]}").json()
    r = client.post(f"/api/images/{original['id']}/regenerate", json={"keep_seed": True})
    assert r.status_code == 202
    job2 = wait_job(client, r.json()["job_id"])
    assert job2["status"] == "done"
    regen = client.get(f"/api/images/{job2['result_image_ids'][0]}").json()
    keys = (
        "prompt",
        "positive",
        "negative_prompt",
        "seed",
        "steps",
        "cfg",
        "sampler_name",
        "scheduler",
        "width",
        "height",
        "upscale",
        "face_method",
        "face_weight",
        "checkpoint",
        "face_detailer",
        "face_detailer_denoise",
        "scene_ids",
    )
    assert {k: original["params_snapshot"][k] for k in keys} == {
        k: regen["params_snapshot"][k] for k in keys
    }  # acceptance #25
    assert regen["params_snapshot"]["regenerate_of"] == original["id"]
    r = client.post(f"/api/images/{original['id']}/regenerate", json={"keep_seed": False})
    job3 = wait_job(client, r.json()["job_id"])
    assert client.get(f"/api/images/{job3['result_image_ids'][0]}").json()["seed"] != 999


def test_jobs_are_serial_and_cancelable(client: TestClient, mock_comfy) -> None:  # type: ignore[no-untyped-def]
    c = create_character(client, identity=92)
    mock_comfy.state.step_delay = 0.15
    mock_comfy.state.steps = 6
    first = client.post(
        "/api/generate", json={"character_id": c["id"], "prompt": "a", "count": 3, "adult_only": True}
    ).json()
    second = client.post(
        "/api/generate", json={"character_id": c["id"], "prompt": "b", "count": 1, "adult_only": True}
    ).json()
    assert first["position"] == 0 and second["position"] == 1
    time.sleep(0.4)
    running = client.get("/api/jobs", params={"status": "running"}).json()["items"]
    assert [j["id"] for j in running] == [first["job_id"]]
    assert client.get(f"/api/jobs/{second['job_id']}").json()["queue_position"] == 1
    r = client.post(f"/api/jobs/{first['job_id']}/cancel")
    assert r.status_code == 200
    job = wait_job(client, first["job_id"])
    assert job["status"] == "canceled"
    assert len(job["result_image_ids"]) < 3
    job2 = wait_job(client, second["job_id"])
    assert job2["status"] == "done"
    # canceling a queued job
    mock_comfy.state.step_delay = 0.2
    third = client.post(
        "/api/generate", json={"character_id": c["id"], "prompt": "c", "count": 2, "adult_only": True}
    ).json()
    fourth = client.post(
        "/api/generate", json={"character_id": c["id"], "prompt": "d", "count": 1, "adult_only": True}
    ).json()
    assert client.post(f"/api/jobs/{fourth['job_id']}/cancel").json()["status"] == "canceled"
    client.post(f"/api/jobs/{third['job_id']}/cancel")
    wait_job(client, third["job_id"])
    assert client.post("/api/jobs/nope/cancel").status_code == 404


def test_comfy_execution_error_is_reported(client: TestClient, mock_comfy) -> None:  # type: ignore[no-untyped-def]
    c = create_character(client, identity=93)
    mock_comfy.state.fail_next = "CUDA out of memory. Tried to allocate 2.00 GiB"
    job = wait_job(
        client,
        client.post(
            "/api/generate", json={"character_id": c["id"], "prompt": "a", "count": 1, "adult_only": True}
        ).json()["job_id"],
    )
    assert job["status"] == "error" and "out of memory" in job["error"]
    assert job["result_image_ids"] == []
