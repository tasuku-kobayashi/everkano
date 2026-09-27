"""Gallery: list / detail / file / thumb / patch / delete / regenerate / similarity / bulk / zip.

`router` is header-authenticated; `files_router` (file / thumb bytes, GET only) also accepts the `psk` cookie so the
browser can load `<img src>` without a header.
"""

from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.container import Services
from app.face import cosine_similarity
from app.ids import new_ulid
from app.models import IDENTITY_LOCKED_KEYS, ImageRecord
from app.routers.common import ServicesDep, bad_request, not_found
from app.routers.generate import GenerationSpec, prepare_generate_job
from app.schemas import (
    BulkResponse,
    DeleteResponse,
    ImageBulkDeleteRequest,
    ImageBulkPatchRequest,
    ImageDetail,
    ImageListResponse,
    ImagePatchRequest,
    ImageZipRequest,
    JobAccepted,
    RegenerateRequest,
    SimilarityResponse,
)
from app.security import ApiKeyDep, require_api_key, require_api_key_or_cookie
from app.serialize import image_detail, image_schema

router = APIRouter(prefix="/images", tags=["images"], dependencies=[Depends(require_api_key)])
files_router = APIRouter(prefix="/images", tags=["images"], dependencies=[Depends(require_api_key_or_cookie)])

FILE_CACHE_HEADERS = {"Cache-Control": "private, max-age=86400"}


def _image_or_404(s: Services, image_id: str) -> ImageRecord:
    image = s.db.get_image(image_id)
    if image is None:
        raise not_found("画像が見つかりません")
    return image


def _clean_tags(tags: list[str]) -> list[str]:
    return [t.strip() for t in tags if t.strip()]


@router.get("", response_model=ImageListResponse)
async def list_images(
    s: ServicesDep,
    character_id: str | None = None,
    kind: Annotated[str | None, Query(pattern="^(generated|draft|verify|upload)$")] = None,
    job_id: str | None = None,
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
    min_similarity: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    favorite: bool | None = None,
    tag: str | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    seed: int | None = None,
    face_method: Annotated[str | None, Query(pattern="^(pulid|faceid|instantid)$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ImageListResponse:
    items, total = s.db.list_images(
        character_id=character_id,
        kind=kind,
        job_id=job_id,
        created_from=from_,
        created_to=to,
        min_similarity=min_similarity,
        favorite=favorite,
        tag=tag,
        q=q,
        seed=seed,
        face_method=face_method,
        limit=limit,
        offset=offset,
    )
    return ImageListResponse(items=[image_schema(i) for i in items], total=total, limit=limit, offset=offset)


@router.get("/{image_id}", response_model=ImageDetail)
async def get_image(image_id: str, s: ServicesDep) -> ImageDetail:
    return image_detail(_image_or_404(s, image_id))


@files_router.get("/{image_id}/file")
async def image_file(image_id: str, s: ServicesDep) -> FileResponse:
    image = _image_or_404(s, image_id)
    if not Path(image.path).is_file():
        raise not_found("画像ファイルがありません")
    return FileResponse(image.path, media_type="image/png", headers=FILE_CACHE_HEADERS)


@files_router.get("/{image_id}/thumb")
async def image_thumb(image_id: str, s: ServicesDep) -> FileResponse:
    image = _image_or_404(s, image_id)
    path = Path(image.thumbnail_path)
    if not path.is_file():
        if not Path(image.path).is_file():
            raise not_found("画像ファイルがありません")
        await asyncio.to_thread(s.storage.make_thumbnail, Path(image.path), path)
    return FileResponse(str(path), media_type="image/webp", headers=FILE_CACHE_HEADERS)


@router.patch("/{image_id}", response_model=ImageDetail)
async def patch_image(image_id: str, body: ImagePatchRequest, s: ServicesDep) -> ImageDetail:
    image = _image_or_404(s, image_id)
    if body.favorite is not None:
        image.favorite = body.favorite
    if body.rating is not None:
        image.rating = body.rating
    if body.tags is not None:
        image.tags = _clean_tags(body.tags)
    s.db.update_image_flags(image)
    return image_detail(image)


@router.delete("/{image_id}", response_model=DeleteResponse)
async def delete_image(image_id: str, s: ServicesDep) -> DeleteResponse:
    image = _image_or_404(s, image_id)
    deleted = s.db.soft_delete_images([image.id])
    return DeleteResponse(deleted=deleted == 1, deleted_images=deleted)


@router.post("/bulk", response_model=BulkResponse)
async def bulk_patch(body: ImageBulkPatchRequest, s: ServicesDep) -> BulkResponse:
    images = s.db.get_images(body.image_ids)
    remove = set(_clean_tags(body.remove_tags))
    add = _clean_tags(body.add_tags)
    for image in images:
        if body.favorite is not None:
            image.favorite = body.favorite
        tags = [t for t in image.tags if t not in remove]
        tags.extend(t for t in add if t not in tags)
        image.tags = tags
        s.db.update_image_flags(image)
    return BulkResponse(updated=len(images))


@router.post("/bulk-delete", response_model=BulkResponse)
async def bulk_delete(body: ImageBulkDeleteRequest, s: ServicesDep) -> BulkResponse:
    return BulkResponse(updated=s.db.soft_delete_images(body.image_ids))


def _build_zip(zip_path: Path, images: list[ImageRecord]) -> None:
    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
            for image in images:
                src = Path(image.path)
                if src.is_file():
                    zf.write(src, arcname=f"{image.id}.png")
                zf.writestr(
                    f"{image.id}.json",
                    json.dumps({**image.params_snapshot, "similarity": image.similarity}, ensure_ascii=False, indent=2),
                )
    except BaseException:
        zip_path.unlink(missing_ok=True)
        raise


@router.post("/zip")
async def zip_images(body: ImageZipRequest, s: ServicesDep) -> FileResponse:
    """Download the selected images plus their params_snapshot JSON as one ZIP (built in DATA_DIR/tmp, removed after
    the response; `Storage.sweep_tmp` collects leftovers of clients that went away)."""
    images = s.db.get_images(body.image_ids)
    if not images:
        raise not_found("画像が見つかりません")
    zip_path = s.storage.data_dir / "tmp" / f"portrait-studio-{new_ulid()}.zip"
    await asyncio.to_thread(_build_zip, zip_path, images)
    return FileResponse(
        str(zip_path),
        media_type="application/zip",
        filename=zip_path.name,
        background=BackgroundTask(s.storage.remove, zip_path),
    )


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


@router.post("/{image_id}/regenerate", response_model=JobAccepted, status_code=202)
async def regenerate(image_id: str, body: RegenerateRequest, s: ServicesDep, api_key_id: ApiKeyDep) -> JobAccepted:
    """Re-run the params_snapshot of an image against the SAME character version it was made with.

    Goes through `prepare_generate_job`, so every guard of POST /api/generate (draft, workflow, denoise, dimensions,
    hires_max, scenes, VRAM) applies here as well. Locked values that were overridden when the image was generated are
    re-applied as explicit overrides — reproducing the image is the point — but never saved as a new version."""
    image = _image_or_404(s, image_id)
    snap = image.params_snapshot or {}
    if image.kind not in ("generated", "verify") or not image.character_id:
        raise bad_request("この画像は再生成できません（キャラクターの生成物ではありません）")
    character = s.db.get_character(image.character_id)
    if character is None:
        raise not_found("キャラクターが削除されています")
    version_no = int(image.character_version or character.current_version)
    version = s.db.get_version(character.id, version_no)
    if version is None:
        raise not_found("生成時の版が見つかりません")
    overrides = {
        key: snap[key]
        for key in IDENTITY_LOCKED_KEYS
        if snap.get(key) is not None and snap[key] != getattr(version.locked, key)
    }
    spec = GenerationSpec(
        prompt=str(snap.get("prompt") or ""),
        count=body.count,
        seed=int(image.seed) if body.keep_seed and image.seed is not None else -1,
        negative_prompt=snap.get("negative_prompt"),
        width=_optional_int(snap.get("width")),
        height=_optional_int(snap.get("height")),
        upscale=_optional_float(snap.get("upscale")),
        face_detailer=bool(snap.get("face_detailer", True)),
        scene_ids=[str(x) for x in snap.get("scene_ids") or []],
        steps=_optional_int(snap.get("steps")),
        cfg=_optional_float(snap.get("cfg")),
        sampler_name=snap.get("sampler_name"),
        scheduler=snap.get("scheduler"),
        hires_denoise=_optional_float(snap.get("hires_denoise")),
        overrides=overrides,
        allow_locked_override=bool(overrides),
        save_as_version=False,
        regenerate_of=image.id,
    )
    job = await prepare_generate_job(s, character=character, version=version, spec=spec, api_key_id=api_key_id)
    return JobAccepted(job_id=job.id, position=s.queue.submit(job))


def _no_reference(image: ImageRecord, reason: str) -> SimilarityResponse:
    return SimilarityResponse(
        image_id=image.id, similarity=None, status="no_reference", reason=reason, reference_id=None, grade="unknown"
    )


@router.get("/{image_id}/similarity", response_model=SimilarityResponse)
async def similarity(image_id: str, s: ServicesDep) -> SimilarityResponse:
    """Recompute the ArcFace cosine similarity against the primary reference of the version the image was made with."""
    image = _image_or_404(s, image_id)
    if not image.character_id or image.character_version is None:
        return _no_reference(image, "キャラクターに紐づいていない画像です")
    version = s.db.get_version(image.character_id, int(image.character_version))
    primary = version.primary if version else None
    if primary is None:
        return _no_reference(image, "参照顔が見つかりません（版が削除された可能性）")
    await s.ensure_face_engine()
    try:
        analysis = await asyncio.to_thread(s.face.analyze_path, Path(image.path))
    except Exception as exc:
        s.db.set_image_similarity(image.id, None, "error")
        return SimilarityResponse(
            image_id=image.id,
            similarity=None,
            status="error",
            reason=f"顔分析に失敗しました: {exc.__class__.__name__}",
            reference_id=primary.id,
            grade="unknown",
        )
    if analysis.embedding is None:
        s.db.set_image_similarity(image.id, None, "no_face")
        return SimilarityResponse(
            image_id=image.id,
            similarity=None,
            status="no_face",
            reason="生成画像から顔が検出できません（顔検出不可）",
            reference_id=primary.id,
            grade="unknown",
        )
    value = round(cosine_similarity(analysis.embedding, primary.embedding), 4)
    s.db.set_image_similarity(image.id, value, "computed")
    return SimilarityResponse(
        image_id=image.id,
        similarity=value,
        status="computed",
        reason=None,
        reference_id=primary.id,
        grade=s.face.similarity_grade(value),
    )
