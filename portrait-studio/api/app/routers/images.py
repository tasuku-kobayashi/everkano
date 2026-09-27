"""Gallery: list / detail / file / thumb / patch / delete / regenerate / similarity / bulk / zip."""

from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.face import cosine_similarity
from app.ids import new_ulid
from app.models import Job, JobProgress, utcnow
from app.routers.common import ServicesDep, bad_request, not_found, reject_if_dangerous, vram_assessment
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
from app.security import ApiKeyDep, require_api_key
from app.serialize import image_detail, image_schema

router = APIRouter(prefix="/images", tags=["images"], dependencies=[Depends(require_api_key)])


def _image_or_404(s: Any, image_id: str) -> Any:
    image = s.db.get_image(image_id)
    if image is None:
        raise not_found("画像が見つかりません")
    return image


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


@router.get("/{image_id}/file")
async def image_file(image_id: str, s: ServicesDep) -> FileResponse:
    image = _image_or_404(s, image_id)
    if not Path(image.path).is_file():
        raise not_found("画像ファイルがありません")
    return FileResponse(image.path, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/{image_id}/thumb")
async def image_thumb(image_id: str, s: ServicesDep) -> FileResponse:
    image = _image_or_404(s, image_id)
    path = Path(image.thumbnail_path)
    if not path.is_file():
        if not Path(image.path).is_file():
            raise not_found("画像ファイルがありません")
        await asyncio.to_thread(s.storage.make_thumbnail, Path(image.path), path)
    return FileResponse(str(path), media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})


@router.patch("/{image_id}", response_model=ImageDetail)
async def patch_image(image_id: str, body: ImagePatchRequest, s: ServicesDep) -> ImageDetail:
    image = _image_or_404(s, image_id)
    if body.favorite is not None:
        image.favorite = body.favorite
    if body.rating is not None:
        image.rating = body.rating
    if body.tags is not None:
        image.tags = [t.strip() for t in body.tags if t.strip()]
    s.db.update_image(image)
    return image_detail(image)


@router.delete("/{image_id}", response_model=DeleteResponse)
async def delete_image(image_id: str, s: ServicesDep) -> DeleteResponse:
    image = _image_or_404(s, image_id)
    image.deleted_at = utcnow()
    s.db.update_image(image)
    return DeleteResponse(deleted=True, deleted_images=1)


@router.post("/bulk", response_model=BulkResponse)
async def bulk_patch(body: ImageBulkPatchRequest, s: ServicesDep) -> BulkResponse:
    images = s.db.get_images(body.image_ids)
    for image in images:
        if body.favorite is not None:
            image.favorite = body.favorite
        tags = [t for t in image.tags if t not in body.remove_tags]
        for t in body.add_tags:
            if t.strip() and t not in tags:
                tags.append(t.strip())
        image.tags = tags
        s.db.update_image(image)
    return BulkResponse(updated=len(images))


@router.post("/bulk-delete", response_model=BulkResponse)
async def bulk_delete(body: ImageBulkDeleteRequest, s: ServicesDep) -> BulkResponse:
    images = s.db.get_images(body.image_ids)
    now = utcnow()
    for image in images:
        image.deleted_at = now
        s.db.update_image(image)
    return BulkResponse(updated=len(images))


@router.post("/zip")
async def zip_images(body: ImageZipRequest, s: ServicesDep) -> FileResponse:
    """Download the selected images plus their params_snapshot JSON as one ZIP."""
    images = s.db.get_images(body.image_ids)
    if not images:
        raise not_found("画像が見つかりません")
    tmp_dir = s.storage.data_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    zip_path = tmp_dir / f"portrait-studio-{new_ulid()}.zip"

    def build() -> None:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
            for image in images:
                src = Path(image.path)
                if src.is_file():
                    zf.write(src, arcname=f"{image.id}.png")
                zf.writestr(
                    f"{image.id}.json",
                    json.dumps({**image.params_snapshot, "similarity": image.similarity}, ensure_ascii=False, indent=2),
                )

    await asyncio.to_thread(build)
    return FileResponse(
        str(zip_path),
        media_type="application/zip",
        filename=zip_path.name,
        background=BackgroundTask(lambda: s.storage.remove(zip_path)),
    )


@router.post("/{image_id}/regenerate", response_model=JobAccepted, status_code=202)
async def regenerate(image_id: str, body: RegenerateRequest, s: ServicesDep, api_key_id: ApiKeyDep) -> JobAccepted:
    """Re-run the exact params_snapshot of an image (same character version, same locked values)."""
    image = _image_or_404(s, image_id)
    snap = image.params_snapshot or {}
    if image.kind not in ("generated", "verify") or not image.character_id:
        raise bad_request("この画像は再生成できません（キャラクターの生成物ではありません）")
    character = s.db.get_character(image.character_id)
    if character is None:
        raise not_found("キャラクターが削除されています")
    if character.status == "draft":
        raise bad_request("下書きのキャラクターでは生成できません。先に登録してください。")
    version_no = int(image.character_version or character.current_version)
    version = s.db.get_version(character.id, version_no)
    if version is None:
        raise not_found("生成時の版が見つかりません")
    locked = version.locked
    overrides: dict[str, Any] = {}
    for key in (
        "checkpoint",
        "face_method",
        "face_weight",
        "prefix_prompt",
        "face_detailer_denoise",
        "lora",
        "lora_strength",
    ):
        if key in snap and snap[key] is not None and snap[key] != getattr(locked, key):
            overrides[key] = snap[key]
    method = str(snap.get("face_method") or locked.face_method)
    if not s.workflow_usable(method):
        raise bad_request(f"{method} のワークフローが使えません")
    width = int(snap.get("width") or locked.default_width)
    height = int(snap.get("height") or locked.default_height)
    upscale = float(snap.get("upscale") or 1.0)
    face_detailer = bool(snap.get("face_detailer", True))
    assessment = await vram_assessment(
        s, method=method, width=width, height=height, upscale=upscale, face_detailer=face_detailer
    )
    reject_if_dangerous(assessment)
    seed = int(image.seed) if body.keep_seed and image.seed is not None else -1
    request = {
        "character_id": character.id,
        "character_version": version.version,
        "prompt": snap.get("prompt") or "",
        "negative_prompt": snap.get("negative_prompt"),
        "count": body.count,
        "seed": seed,
        "width": width,
        "height": height,
        "upscale": upscale,
        "face_detailer": face_detailer,
        "scene_ids": snap.get("scene_ids", []),
        "steps": snap.get("steps"),
        "cfg": snap.get("cfg"),
        "sampler_name": snap.get("sampler_name"),
        "scheduler": snap.get("scheduler"),
        "hires_denoise": snap.get("hires_denoise"),
        "effective_overrides": overrides,
        "allow_locked_override": bool(overrides),
        "adult_only": True,
        "regenerate_of": image.id,
        "vram_estimate_mb": assessment.estimated_peak_mb,
    }
    job = Job(
        id=new_ulid(),
        type="generate",
        status="queued",
        progress=JobProgress(total_images=body.count),
        request=request,
        result=None,
        result_image_ids=[],
        error=None,
        character_id=character.id,
        api_key_id=api_key_id,
        created_at=utcnow(),
        started_at=None,
        finished_at=None,
    )
    return JobAccepted(job_id=job.id, position=s.queue.submit(job))


@router.get("/{image_id}/similarity", response_model=SimilarityResponse)
async def similarity(image_id: str, s: ServicesDep) -> SimilarityResponse:
    """Recompute the ArcFace cosine similarity against the primary reference of the version the image was made with."""
    image = _image_or_404(s, image_id)
    if not image.character_id or image.character_version is None:
        return SimilarityResponse(
            image_id=image.id,
            similarity=None,
            status="no_reference",
            reason="キャラクターに紐づいていない画像です",
            reference_id=None,
            grade="unknown",
        )
    version = s.db.get_version(image.character_id, int(image.character_version))
    primary = version.primary if version else None
    if primary is None:
        return SimilarityResponse(
            image_id=image.id,
            similarity=None,
            status="no_reference",
            reason="参照顔が見つかりません（版が削除された可能性）",
            reference_id=None,
            grade="unknown",
        )
    await s.ensure_face_engine()
    try:
        analysis = await asyncio.to_thread(s.face.analyze_path, Path(image.path))
    except Exception as exc:
        image.similarity = None
        image.similarity_status = "error"
        s.db.update_image(image)
        return SimilarityResponse(
            image_id=image.id,
            similarity=None,
            status="error",
            reason=f"顔分析に失敗しました: {exc.__class__.__name__}",
            reference_id=primary.id,
            grade="unknown",
        )
    if analysis.embedding is None:
        image.similarity = None
        image.similarity_status = "no_face"
        s.db.update_image(image)
        return SimilarityResponse(
            image_id=image.id,
            similarity=None,
            status="no_face",
            reason="生成画像から顔が検出できません（顔検出不可）",
            reference_id=primary.id,
            grade="unknown",
        )
    value = round(cosine_similarity(analysis.embedding, primary.embedding), 4)
    image.similarity = value
    image.similarity_status = "computed"
    s.db.update_image(image)
    return SimilarityResponse(
        image_id=image.id,
        similarity=value,
        status="computed",
        reason=None,
        reference_id=primary.id,
        grade=s.face.similarity_grade(value),  # type: ignore[arg-type]
    )
