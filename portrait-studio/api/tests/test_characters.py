from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import wait_job
from tests.helpers import face_png, landscape_png, two_faces_png


def upload_and_analyze(client: TestClient, *images: tuple[str, bytes]) -> dict:  # type: ignore[type-arg]
    files = [("files", (name, data, "image/png")) for name, data in images]
    r = client.post("/api/characters/analyze", files=files)
    assert r.status_code == 200, r.text
    return r.json()


def create_character(
    client: TestClient, *, identity: int = 77, name: str = "テスト花子", draft: bool = False, **extra
) -> dict:  # type: ignore[type-arg,no-untyped-def]
    analysis = upload_and_analyze(client, ("ref.png", face_png(identity)))
    image_id = analysis["items"][0]["image_id"]
    body = {
        "name": name,
        "tags": ["test"],
        "description": "テスト用",
        "is_synthetic": True,
        "adult_confirmed": True,
        "reference_image_ids": [image_id],
        "primary_image_id": image_id,
        "locked": {"checkpoint": "mock_photoreal_xl_v1.safetensors", "face_method": "pulid", "face_weight": 0.8},
        "draft": draft,
        **extra,
    }
    r = client.post("/api/characters", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ----------------------------------------------------------------------------- analyze (acceptance #8, #9)
def test_analyze_reports_quality_matrix_clusters_and_recommendation(client: TestClient) -> None:
    body = upload_and_analyze(
        client, ("a.png", face_png(5, seed=1)), ("b.png", face_png(5, seed=2, yaw_deg=30)), ("c.png", face_png(140))
    )
    items = body["items"]
    assert [i["face_count"] for i in items] == [1, 1, 1]
    q = items[0]["quality"]
    for key in ("face_ratio", "det_score", "sharpness", "yaw", "pitch", "roll", "composite"):
        assert key in q
    assert items[1]["warnings"] == ["not_frontal"]
    assert body["similarity_matrix"][0][1] > 0.99 and body["similarity_matrix"][0][2] < 0.3
    assert body["clusters"][0] == [0, 1]
    assert body["recommended_index"] == 0
    assert "正面" in body["recommend_reason"]
    assert body["items"][0]["embedding_id"].startswith("e_")
    assert body["thresholds"]["yaw_max_deg"] == 15.0
    # uploaded files are addressable images
    assert client.get(items[0]["thumb_url"]).status_code == 200
    assert client.get(items[0]["file_url"]).status_code == 200


def test_analyze_rejects_images_without_a_face(client: TestClient) -> None:
    body = upload_and_analyze(client, ("landscape.png", landscape_png()))
    item = body["items"][0]
    assert item["face_count"] == 0 and item["usable"] is False and item["quality"] is None
    assert "no_face" in item["warnings"]
    assert body["recommended_index"] is None
    assert "使用でき" in body["recommend_reason"]


def test_analyze_requires_input_and_rejects_garbage(client: TestClient) -> None:
    assert client.post("/api/characters/analyze").status_code == 400
    r = client.post("/api/characters/analyze", files=[("files", ("x.png", b"not an image", "image/png"))])
    assert r.status_code == 400 and "画像として読めません" in r.json()["detail"]


# ----------------------------------------------------------------------------- draft (acceptance #10)
def test_draft_generates_seed_faces(client: TestClient) -> None:
    r = client.post("/api/characters/draft", json={"prompt": "japanese woman, portrait, natural light", "count": 4})
    assert r.status_code == 202 and r.json()["job_id"]
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "done" and len(job["result_image_ids"]) == 4
    assert job["progress"]["total_images"] == 4 and job["progress"]["current"] == 4
    img = client.get(f"/api/images/{job['result_image_ids'][0]}").json()
    assert img["kind"] == "draft" and img["character_id"] is None and img["similarity"] is None
    assert img["params_snapshot"]["checkpoint"] == "mock_photoreal_xl_v1.safetensors"
    assert img["params_snapshot"]["positive"].startswith("photorealistic, RAW photo")
    assert img["params_snapshot"]["positive"].endswith("japanese woman, portrait, natural light")


def test_draft_rejects_unknown_checkpoint_at_comfy(client: TestClient) -> None:
    r = client.post("/api/characters/draft", json={"prompt": "x", "count": 1, "checkpoint": "missing.safetensors"})
    assert r.status_code == 202
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "error" and "ckpt_name" in job["error"]


# ----------------------------------------------------------------------------- registration (acceptance #11)
def test_registration_requires_synthetic_and_adult_declarations(client: TestClient) -> None:
    r = client.post(
        "/api/characters",
        json={
            "name": "X",
            "is_synthetic": False,
            "adult_confirmed": True,
            "reference_image_ids": ["i_1"],
            "locked": {},
        },
    )
    assert r.status_code == 400 and "is_synthetic" in r.json()["detail"]
    r = client.post(
        "/api/characters",
        json={
            "name": "X",
            "is_synthetic": True,
            "adult_confirmed": False,
            "reference_image_ids": ["i_1"],
            "locked": {},
        },
    )
    assert r.status_code == 400 and "adult_confirmed" in r.json()["detail"]
    r = client.post(
        "/api/characters",
        json={
            "name": "X",
            "is_synthetic": True,
            "adult_confirmed": True,
            "reference_image_ids": ["i_1"],
            "locked": {"checkpoint": "c"},
        },
    )
    assert r.status_code == 400 and "画像が見つかりません" in r.json()["detail"]


def test_registration_rejects_faceless_and_multi_face_references(client: TestClient) -> None:
    faceless = upload_and_analyze(client, ("l.png", landscape_png()))["items"][0]["image_id"]
    r = client.post(
        "/api/characters",
        json={
            "name": "X",
            "is_synthetic": True,
            "adult_confirmed": True,
            "reference_image_ids": [faceless],
            "locked": {"checkpoint": "c"},
        },
    )
    assert r.status_code == 400 and "使用不可" in r.json()["detail"]
    multi = upload_and_analyze(client, ("m.png", two_faces_png(1, 2)))["items"][0]["image_id"]
    r = client.post(
        "/api/characters",
        json={
            "name": "X",
            "is_synthetic": True,
            "adult_confirmed": True,
            "reference_image_ids": [multi],
            "locked": {"checkpoint": "c"},
        },
    )
    assert r.status_code == 400 and "複数の顔" in r.json()["detail"]


def test_create_get_list_patch(client: TestClient) -> None:
    c = create_character(client)
    assert c["status"] == "active" and c["current_version"] == 1 and c["locked"]["face_method"] == "pulid"
    assert c["references"][0]["is_primary"] and c["references"][0]["quality"]["face_count"] == 1
    assert c["thumbnail_url"].endswith("/file")
    assert client.get(c["thumbnail_url"]).status_code == 200
    detail = client.get(f"/api/characters/{c['id']}").json()
    assert detail["name"] == "テスト花子" and detail["versions"] == [1] and detail["stats"]["generations"] == 0
    listing = client.get("/api/characters", params={"q": "花子", "sort": "name"}).json()["items"]
    assert [x["id"] for x in listing] == [c["id"]]
    assert client.get("/api/characters", params={"tag": "nope"}).json()["items"] == []
    patched = client.patch(f"/api/characters/{c['id']}", json={"name": "花子2", "tags": ["a", "b"]}).json()
    assert patched["name"] == "花子2" and patched["tags"] == ["a", "b"]
    # locked / references cannot be changed through PATCH
    r = client.patch(f"/api/characters/{c['id']}", json={"locked": {"face_weight": 0.1}})
    assert r.status_code == 422
    assert client.get("/api/characters/nope").status_code == 404


def test_default_checkpoint_resolution(client: TestClient) -> None:
    c = create_character(client, locked={"face_method": "faceid"})
    assert c["locked"]["checkpoint"] == "mock_photoreal_xl_v1.safetensors"  # first installed checkpoint
    assert c["locked"]["face_method"] == "faceid"


# ----------------------------------------------------------------------------- draft characters + register (wizard step 3/4)
def test_draft_character_verify_then_register(client: TestClient) -> None:
    c = create_character(client, name="", draft=True, is_synthetic=False, adult_confirmed=False)
    assert c["status"] == "draft" and c["name"] == "（下書き）"
    assert client.get("/api/characters").json()["items"] == []
    assert [x["id"] for x in client.get("/api/characters", params={"include_drafts": True}).json()["items"]] == [
        c["id"]
    ]
    # drafts cannot generate
    r = client.post("/api/generate", json={"character_id": c["id"], "prompt": "x", "count": 1, "adult_only": True})
    assert r.status_code == 400 and "下書き" in r.json()["detail"]
    # but can verify (acceptance #12): 3 weights x 3 scenes = 9 images with similarity
    r = client.post(
        f"/api/characters/{c['id']}/verify",
        json={
            "face_method": "pulid",
            "face_weights": [0.6, 0.8, 1.0],
            "scenes": ["portrait_closeup", "upper_body_cafe", "full_body_street"],
        },
    )
    assert r.status_code == 202, r.text
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    assert len(job["result_image_ids"]) == 9
    items = job["result"]["items"]
    assert {(i["scene"], i["face_weight"]) for i in items} == {
        (s, w) for s in ("portrait_closeup", "upper_body_cafe", "full_body_street") for w in (0.6, 0.8, 1.0)
    }
    assert all(i["similarity"] is not None for i in items)
    by_weight = job["result"]["by_weight"]
    assert (
        by_weight["0.8"] > 0.99 and by_weight["1"] > 0.99 and by_weight["0.6"] < 0.5
    )  # mock: low weight -> other person
    assert job["result"]["best_weight"] in (0.8, 1.0)
    # register: declarations mandatory
    r = client.post(
        f"/api/characters/{c['id']}/register", json={"name": "花子", "is_synthetic": False, "adult_confirmed": True}
    )
    assert r.status_code == 400
    r = client.post(
        f"/api/characters/{c['id']}/register",
        json={
            "name": "花子",
            "tags": ["t"],
            "is_synthetic": True,
            "adult_confirmed": True,
            "locked": {"face_method": "pulid", "face_weight": 1.0},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "active" and body["locked"]["face_weight"] == 1.0 and body["name"] == "花子"
    assert (
        client.post(
            f"/api/characters/{c['id']}/register", json={"name": "x", "is_synthetic": True, "adult_confirmed": True}
        ).status_code
        == 400
    )


def test_verify_validates_inputs(client: TestClient) -> None:
    c = create_character(client)
    r = client.post(f"/api/characters/{c['id']}/verify", json={"scenes": ["no_such_scene"]})
    assert r.status_code == 400 and "no_such_scene" in r.json()["detail"]
    assert client.post("/api/characters/nope/verify", json={}).status_code == 404


# ----------------------------------------------------------------------------- versions / rollback / delete
def test_versions_rollback_and_images_keep_version(client: TestClient) -> None:
    c = create_character(client, identity=30)
    new_ref = upload_and_analyze(client, ("ref2.png", face_png(31)))["items"][0]["image_id"]
    r = client.post(
        f"/api/characters/{c['id']}/versions",
        json={"reference_image_ids": [new_ref], "note": "正面の良い1枚に差し替え", "locked": {"face_weight": 0.9}},
    )
    assert r.status_code == 201, r.text
    v2 = r.json()
    assert v2["version"] == 2 and v2["note"] == "正面の良い1枚に差し替え" and v2["locked"]["face_weight"] == 0.9
    detail = client.get(f"/api/characters/{c['id']}").json()
    assert detail["current_version"] == 2 and detail["versions"] == [1, 2]
    versions = client.get(f"/api/characters/{c['id']}/versions").json()
    assert versions["current_version"] == 2 and [v["version"] for v in versions["items"]] == [1, 2]
    # generate with v2, then roll back to v1 and generate again: images carry their version
    job = wait_job(
        client,
        client.post(
            "/api/generate", json={"character_id": c["id"], "prompt": "cafe", "count": 1, "adult_only": True}
        ).json()["job_id"],
    )
    assert job["status"] == "done"
    img2 = client.get(f"/api/images/{job['result_image_ids'][0]}").json()
    assert img2["character_version"] == 2
    assert client.post(f"/api/characters/{c['id']}/rollback/1").json()["current_version"] == 1
    assert client.post(f"/api/characters/{c['id']}/rollback/9").status_code == 404
    job = wait_job(
        client,
        client.post(
            "/api/generate", json={"character_id": c["id"], "prompt": "cafe", "count": 1, "adult_only": True}
        ).json()["job_id"],
    )
    img1 = client.get(f"/api/images/{job['result_image_ids'][0]}").json()
    assert img1["character_version"] == 1


def test_delete_character_keeps_or_deletes_images(client: TestClient, settings) -> None:  # type: ignore[no-untyped-def]
    c = create_character(client, identity=50)
    job = wait_job(
        client,
        client.post(
            "/api/generate", json={"character_id": c["id"], "prompt": "x", "count": 2, "adult_only": True}
        ).json()["job_id"],
    )
    ids = job["result_image_ids"]
    ref_path = Path(settings.data_dir) / "refs" / c["id"]
    assert ref_path.is_dir()
    r = client.delete(f"/api/characters/{c['id']}", params={"delete_images": False})
    assert r.status_code == 200 and r.json()["deleted_references"] == 1 and r.json()["deleted_images"] == 0
    assert not ref_path.exists()
    assert client.get(f"/api/characters/{c['id']}").status_code == 404
    kept = client.get(f"/api/images/{ids[0]}").json()
    assert kept["character_id"] is None and kept["character_name"] == "テスト花子"

    c2 = create_character(client, identity=51, name="削除太郎")
    job = wait_job(
        client,
        client.post(
            "/api/generate", json={"character_id": c2["id"], "prompt": "x", "count": 1, "adult_only": True}
        ).json()["job_id"],
    )
    image_path = Path(client.get(f"/api/images/{job['result_image_ids'][0]}").json()["params_snapshot"]["image_id"])
    r = client.delete(f"/api/characters/{c2['id']}", params={"delete_images": True})
    assert r.json()["deleted_images"] == 1
    assert client.get(f"/api/images/{job['result_image_ids'][0]}").status_code == 404
    assert not list((Path(settings.data_dir) / "images" / "generated").glob(f"{image_path}*"))


def test_reference_file_and_sidecar_exist(client: TestClient, settings) -> None:  # type: ignore[no-untyped-def]
    c = create_character(client, identity=60)
    ref = c["references"][0]
    assert client.get(ref["file_url"]).headers["content-type"] == "image/png"
    job = wait_job(
        client,
        client.post(
            "/api/generate", json={"character_id": c["id"], "prompt": "x", "count": 1, "adult_only": True}
        ).json()["job_id"],
    )
    image_id = job["result_image_ids"][0]
    png = Path(settings.data_dir) / "images" / "generated" / f"{image_id}.png"
    sidecar = png.with_suffix(".json")
    assert png.is_file() and sidecar.is_file()
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["image_id"] == image_id and data["character_id"] == c["id"] and data["seed"] is not None
    assert data["api_key_id"] and data["endpoint"] == "/api/generate" and data["similarity"] is not None
