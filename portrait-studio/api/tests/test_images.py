from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient

from tests.conftest import wait_job
from tests.test_characters import create_character


def generated(client: TestClient, character_id: str, count: int = 3, **kw) -> list[str]:  # type: ignore[no-untyped-def]
    job = wait_job(
        client,
        client.post(
            "/api/generate",
            json={"character_id": character_id, "prompt": "x", "count": count, "adult_only": True, **kw},
        ).json()["job_id"],
    )
    assert job["status"] == "done"
    return list(job["result_image_ids"])


def test_list_filters_and_pagination(client: TestClient) -> None:
    a = create_character(client, identity=10, name="アルファ")
    b = create_character(client, identity=11, name="ベータ")
    ids_a = generated(client, a["id"], 3, seed=100)
    ids_b = generated(client, b["id"], 2, seed=200)
    all_images = client.get("/api/images", params={"kind": "generated"}).json()
    assert all_images["total"] == 5 and len(all_images["items"]) == 5
    assert all_images["items"][0]["id"] == ids_b[-1]  # newest first
    assert client.get("/api/images", params={"character_id": a["id"]}).json()["total"] == 3
    assert client.get("/api/images", params={"seed": 201}).json()["items"][0]["id"] == ids_b[1]
    assert client.get("/api/images", params={"face_method": "pulid"}).json()["total"] == 5
    assert client.get("/api/images", params={"face_method": "instantid"}).json()["total"] == 0
    assert client.get("/api/images", params={"min_similarity": 0.5}).json()["total"] == 5
    assert client.get("/api/images", params={"q": "ベータ"}).json()["total"] == 2  # character name
    assert client.get("/api/images", params={"q": "RAW photo"}).json()["total"] == 5  # prefix in positive
    page = client.get("/api/images", params={"limit": 2, "offset": 2, "kind": "generated"}).json()
    assert page["total"] == 5 and len(page["items"]) == 2 and page["offset"] == 2
    client.patch(f"/api/images/{ids_a[0]}", json={"favorite": True, "rating": 5, "tags": ["best", "cafe"]})
    assert client.get("/api/images", params={"favorite": True}).json()["items"][0]["id"] == ids_a[0]
    assert client.get("/api/images", params={"tag": "cafe"}).json()["total"] == 1
    detail = client.get(f"/api/images/{ids_a[0]}").json()
    assert detail["favorite"] is True and detail["rating"] == 5 and detail["tags"] == ["best", "cafe"]
    assert client.get("/api/images/nope").status_code == 404


def test_file_thumb_delete_bulk_zip(client: TestClient) -> None:
    c = create_character(client, identity=12)
    ids = generated(client, c["id"], 3)
    assert client.get(f"/api/images/{ids[0]}/file").headers["content-type"] == "image/png"
    assert client.get(f"/api/images/{ids[0]}/thumb").headers["content-type"] == "image/webp"
    assert client.delete(f"/api/images/{ids[0]}").json()["deleted"] is True
    assert client.get(f"/api/images/{ids[0]}").status_code == 404  # soft-deleted
    assert client.get("/api/images", params={"character_id": c["id"]}).json()["total"] == 2
    r = client.post("/api/images/bulk", json={"image_ids": ids[1:], "favorite": True, "add_tags": ["batch"]})
    assert r.json()["updated"] == 2
    assert all(
        i["favorite"] and "batch" in i["tags"]
        for i in client.get("/api/images", params={"character_id": c["id"]}).json()["items"]
    )
    r = client.post("/api/images/zip", json={"image_ids": ids[1:]})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = sorted(zf.namelist())
        assert names == sorted([f"{i}.png" for i in ids[1:]] + [f"{i}.json" for i in ids[1:]])
        assert b'"seed"' in zf.read(f"{ids[1]}.json")
    assert client.post("/api/images/bulk-delete", json={"image_ids": ids[1:]}).json()["updated"] == 2
    assert client.get("/api/images", params={"character_id": c["id"]}).json()["total"] == 0


def test_similarity_endpoint_recomputes(client: TestClient) -> None:
    c = create_character(client, identity=13)
    ids = generated(client, c["id"], 1)
    r = client.get(f"/api/images/{ids[0]}/similarity")  # acceptance #16
    assert r.status_code == 200
    body = r.json()
    assert (
        body["status"] == "computed" and body["similarity"] > 0.99 and body["grade"] == "good" and body["reference_id"]
    )
    draft = wait_job(client, client.post("/api/characters/draft", json={"prompt": "seed", "count": 1}).json()["job_id"])
    body = client.get(f"/api/images/{draft['result_image_ids'][0]}/similarity").json()
    assert body["status"] == "no_reference" and body["similarity"] is None and body["reason"]
    # a low-weight generation is "another person" in the mock -> warning grade
    r = client.post(
        "/api/generate",
        json={
            "character_id": c["id"],
            "prompt": "x",
            "count": 1,
            "adult_only": True,
            "overrides": {"face_weight": 0.3},
            "allow_locked_override": True,
        },
    )
    job = wait_job(client, r.json()["job_id"])
    body = client.get(f"/api/images/{job['result_image_ids'][0]}/similarity").json()
    assert body["grade"] == "warning" and body["similarity"] < 0.6


def test_regenerate_rejects_drafts_and_uploads(client: TestClient) -> None:
    draft = wait_job(client, client.post("/api/characters/draft", json={"prompt": "seed", "count": 1}).json()["job_id"])
    r = client.post(f"/api/images/{draft['result_image_ids'][0]}/regenerate", json={})
    assert r.status_code == 400
