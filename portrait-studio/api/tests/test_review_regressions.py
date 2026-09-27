"""Regression tests for the acceptance-inspection findings (auth scope, purge, lost updates, upload limits, history)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.db import Database
from app.models import Character, utcnow
from app.routers import characters as characters_router
from tests.conftest import wait_job
from tests.helpers import face_png
from tests.test_characters import create_character
from tests.test_images import generated


# ----------------------------------------------------------------------------- auth scope (F1)
def test_cookie_is_accepted_only_by_file_routes(client: TestClient) -> None:
    c = create_character(client, identity=30)
    ids = generated(client, c["id"], 1)
    client.headers.pop("X-API-Key")
    client.cookies.set("psk", "test-api-key-0123456789")
    assert client.get(f"/api/images/{ids[0]}/file").status_code == 200
    assert client.get(f"/api/images/{ids[0]}/thumb").status_code == 200
    assert client.get(c["references"][0]["file_url"]).status_code == 200
    assert client.get(f"/api/images/{ids[0]}").status_code == 401
    assert client.get("/api/images").status_code == 401
    assert client.patch(f"/api/images/{ids[0]}", json={"favorite": True}).status_code == 401
    assert client.post("/api/images/bulk-delete", json={"image_ids": ids}).status_code == 401
    assert client.post(f"/api/images/{ids[0]}/regenerate", json={}).status_code == 401
    client.cookies.set("psk", "wrong")
    assert client.get(f"/api/images/{ids[0]}/file").status_code == 401


# ----------------------------------------------------------------------------- purge (F7 / F9)
def test_delete_character_purges_soft_deleted_images_too(client: TestClient, settings: Settings) -> None:
    c = create_character(client, identity=31)
    ids = generated(client, c["id"], 2)
    assert client.delete(f"/api/images/{ids[0]}").json()["deleted"] is True  # hidden from the gallery, still on disk
    files = [Path(settings.data_dir) / "images" / "generated" / f"{i}.png" for i in ids]
    thumbs = [Path(settings.data_dir) / "thumbs" / f"{i}.webp" for i in ids]
    assert all(f.is_file() for f in files) and all(t.is_file() for t in thumbs)
    r = client.delete(f"/api/characters/{c['id']}", params={"delete_images": True})
    assert r.status_code == 200 and r.json()["deleted_images"] == 2
    assert not any(f.exists() for f in files) and not any(t.exists() for t in thumbs)
    assert not any(f.with_suffix(".json").exists() for f in files)
    assert client.get("/api/images", params={"character_id": c["id"]}).json()["total"] == 0


def test_delete_character_detaches_soft_deleted_images_when_kept(client: TestClient, settings: Settings) -> None:
    c = create_character(client, identity=32)
    ids = generated(client, c["id"], 1)
    client.delete(f"/api/images/{ids[0]}")
    client.delete(f"/api/characters/{c['id']}", params={"delete_images": False})
    db = Database(Path(settings.data_dir) / "portrait_studio.sqlite3")
    row = db.get_image(ids[0], include_deleted=True)
    assert row is not None and row.character_id is None and row.character_name == "テスト花子"


# ----------------------------------------------------------------------------- lost update (F6)
def test_update_character_never_overwrites_counters(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.sqlite3")
    now = utcnow()
    character = Character(
        id="c1",
        name="a",
        tags=[],
        description="",
        status="active",
        is_synthetic=True,
        adult_confirmed=True,
        current_version=1,
        generation_count=0,
        last_used_at=None,
        created_at=now,
        updated_at=now,
    )
    db.insert_character(character)
    stale = db.get_character("c1")
    assert stale is not None
    db.record_generation("c1", 3)  # the worker finishes a job while a PATCH holds a stale copy
    stale.name = "renamed"
    db.update_character(stale)
    fresh = db.get_character("c1")
    assert fresh is not None and fresh.name == "renamed" and fresh.generation_count == 3
    assert fresh.last_used_at is not None
    db.set_current_version("c1", 2)
    fresh = db.get_character("c1")
    assert fresh is not None and fresh.current_version == 2 and fresh.generation_count == 3


def test_patch_character_after_generation_keeps_generation_count(client: TestClient) -> None:
    c = create_character(client, identity=33)
    generated(client, c["id"], 2)
    r = client.patch(f"/api/characters/{c['id']}", json={"name": "改名"})
    assert r.status_code == 200
    detail = client.get(f"/api/characters/{c['id']}").json()
    assert detail["name"] == "改名" and detail["stats"]["generations"] == 2


# ----------------------------------------------------------------------------- uploads (F2 / F3 / F14)
def test_analyze_rejects_oversized_upload_without_buffering(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(characters_router, "MAX_UPLOAD_BYTES", 64 * 1024)
    monkeypatch.setattr(characters_router, "UPLOAD_CHUNK_BYTES", 4 * 1024)
    big = face_png(3, width=1200, height=1400)
    assert len(big) > 64 * 1024
    r = client.post("/api/characters/analyze", files=[("files", ("big.png", big, "image/png"))])
    assert r.status_code == 413 and "大きすぎます" in r.json()["detail"]
    small = face_png(3, width=160, height=200)
    assert len(small) <= 64 * 1024
    assert client.post("/api/characters/analyze", files=[("files", ("ok.png", small, "image/png"))]).status_code == 200


def test_analyze_rejects_too_many_pixels_and_too_many_files(client: TestClient, settings: Settings) -> None:
    buf = io.BytesIO()
    Image.new("1", (6000, 5000)).save(buf, format="PNG")  # 30 MP but only a few KB on the wire
    assert len(buf.getvalue()) < 64 * 1024
    r = client.post("/api/characters/analyze", files=[("files", ("bomb.png", buf.getvalue(), "image/png"))])
    assert r.status_code == 400 and "メガピクセル" in r.json()["detail"]
    assert not list((Path(settings.data_dir) / "images" / "upload").glob("*.png"))  # nothing left behind
    files = [("files", (f"{i}.png", face_png(3), "image/png")) for i in range(characters_router.MAX_UPLOAD_FILES + 1)]
    assert client.post("/api/characters/analyze", files=files).status_code == 413


def test_unreadable_upload_leaves_no_files(client: TestClient, settings: Settings) -> None:
    r = client.post("/api/characters/analyze", files=[("files", ("x.png", b"not a png at all", "image/png"))])
    assert r.status_code == 400
    assert not list((Path(settings.data_dir) / "images" / "upload").glob("*"))
    assert not list((Path(settings.data_dir) / "thumbs").glob("*"))


# ----------------------------------------------------------------------------- history wait (F8)
def test_lost_history_entry_fails_the_job_instead_of_hanging(client: TestClient, mock_comfy) -> None:  # type: ignore[no-untyped-def]
    c = create_character(client, identity=34)
    services = client.app.state.services
    services.comfy.history_timeout = 0.3
    mock_comfy.state.drop_history = True
    r = client.post("/api/generate", json={"character_id": c["id"], "prompt": "x", "count": 1, "adult_only": True})
    job = wait_job(client, r.json()["job_id"], timeout=15)
    assert job["status"] == "error" and "履歴" in job["error"]
    mock_comfy.state.drop_history = False
    assert services.queue.alive  # the worker loop survived the failure
    assert generated(client, c["id"], 1)  # and keeps serving jobs


# ----------------------------------------------------------------------------- regenerate goes through the guards (F11)
def test_regenerate_enforces_generate_guards(client: TestClient, mock_comfy) -> None:  # type: ignore[no-untyped-def]
    c = create_character(client, identity=35)
    ids = generated(client, c["id"], 1)
    mock_comfy.state.missing_classes = {"PulidFluxModelLoader", "ApplyPulid", "PulidModelLoader"}
    services = client.app.state.services
    services.workflow_issues["pulid"] = ["error: node missing"]
    try:
        r = client.post(f"/api/images/{ids[0]}/regenerate", json={})
        assert r.status_code == 400 and "使えません" in r.json()["detail"]
    finally:
        services.workflow_issues.pop("pulid", None)
        mock_comfy.state.missing_classes = set()
    r = client.post(f"/api/images/{ids[0]}/regenerate", json={"count": 99})
    assert r.status_code == 422
