"""Characters: registry, versions, reference analysis (wizard step 2), seed drafts (step 1), verification (step 3)."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse

from app.container import Services
from app.face import FaceAnalysisResult, embedding_id
from app.ids import new_ulid
from app.models import (
    Character,
    CharacterVersion,
    ImageRecord,
    Job,
    JobProgress,
    LockedParams,
    Quality,
    Reference,
    utcnow,
)
from app.routers.common import (
    ServicesDep,
    bad_request,
    not_found,
    reject_if_dangerous,
    resolve_checkpoint,
    validate_dimensions,
    vram_assessment,
)
from app.schemas import (
    AnalyzeItem,
    AnalyzeResponse,
    CharacterCreateRequest,
    CharacterDetail,
    CharacterListResponse,
    CharacterPatchRequest,
    CharacterRegisterRequest,
    CharacterVersionSchema,
    DeleteResponse,
    DraftRequest,
    JobAccepted,
    LockedParamsPatch,
    QualitySchema,
    VerifyRequest,
    VersionCreateRequest,
    VersionListResponse,
)
from app.security import ApiKeyDep, require_api_key
from app.serialize import character_detail, character_summary, image_file_url, image_thumb_url, version_schema

router = APIRouter(prefix="/characters", tags=["characters"], dependencies=[Depends(require_api_key)])

MAX_UPLOAD_BYTES = 40 * 1024 * 1024
DRAFT_NAME = "（下書き）"


# ----------------------------------------------------------------------------- helpers
def _character_or_404(s: Services, character_id: str) -> Character:
    character = s.db.get_character(character_id)
    if character is None:
        raise not_found("キャラクターが見つかりません")
    return character


def _current_version(s: Services, character: Character) -> CharacterVersion | None:
    return s.db.get_version(character.id, character.current_version)


def _apply_patch(base: LockedParams, patch: LockedParamsPatch | None) -> LockedParams:
    if patch is None:
        return base
    return LockedParams.from_dict({**base.to_dict(), **patch.model_dump(exclude_none=True)})


async def _analyze_image_records(s: Services, images: list[ImageRecord]) -> list[FaceAnalysisResult]:
    await s.ensure_face_engine()
    return [await asyncio.to_thread(s.face.analyze_path, Path(img.path)) for img in images]


async def _build_references(
    s: Services, character_id: str, version: int, image_ids: list[str], primary_image_id: str | None
) -> list[Reference]:
    if not image_ids:
        raise bad_request("参照顔の画像（reference_image_ids）を 1 枚以上指定してください")
    images = s.db.get_images(image_ids)
    found = {img.id for img in images}
    missing = [i for i in image_ids if i not in found]
    if missing:
        raise bad_request(f"画像が見つかりません: {', '.join(missing)}")
    if primary_image_id is not None and primary_image_id not in found:
        raise bad_request("primary_image_id が reference_image_ids に含まれていません")
    analyses = await _analyze_image_records(s, images)
    refs: list[Reference] = []
    ref_dir = s.storage.ref_dir(character_id, version)
    for position, (img, analysis) in enumerate(zip(images, analyses, strict=True)):
        if analysis.quality.face_count == 0 or analysis.embedding is None:
            raise bad_request(f"顔が検出できない画像は参照顔に使えません（使用不可）: {img.id}")
        if analysis.quality.face_count > 1:
            raise bad_request(f"複数の顔が写っている画像は参照顔に使えません（1 人だけの画像にしてください）: {img.id}")
        ref_id = new_ulid()
        dest = ref_dir / f"{ref_id}.png"
        shutil.copyfile(img.path, dest)
        refs.append(
            Reference(
                id=ref_id,
                character_id=character_id,
                version=version,
                position=position,
                image_path=str(dest),
                source_image_id=img.id,
                embedding=analysis.embedding,
                quality=analysis.quality,
                is_primary=(img.id == primary_image_id) if primary_image_id else position == 0,
            )
        )
    return refs


# ----------------------------------------------------------------------------- list / create / detail
@router.get("", response_model=CharacterListResponse)
async def list_characters(
    s: ServicesDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    tag: Annotated[str | None, Query(max_length=50)] = None,
    sort: Annotated[str, Query(pattern="^(recent|name|generations)$")] = "recent",
    include_drafts: bool = False,
) -> CharacterListResponse:
    items = []
    for character in s.db.list_characters(q=q, tag=tag, sort=sort, include_drafts=include_drafts):
        items.append(character_summary(character, _current_version(s, character)))
    return CharacterListResponse(items=items)


@router.post("", response_model=CharacterDetail, status_code=201)
async def create_character(body: CharacterCreateRequest, s: ServicesDep) -> CharacterDetail:
    if not body.draft:
        if not body.is_synthetic:
            raise bad_request(
                "is_synthetic: true（実在の人物ではない架空のキャラクターである）の申告が必須です。"
                "実在人物の顔は使用できません。"
            )
        if not body.adult_confirmed:
            raise bad_request("adult_confirmed: true（成人キャラクターである）の確認が必須です。")
        if not body.name.strip():
            raise bad_request("name は必須です")
    checkpoint = await resolve_checkpoint(s, body.locked.checkpoint if body.locked else None)
    locked = _apply_patch(LockedParams(checkpoint=checkpoint), body.locked)
    if locked.face_method not in s.workflows:
        raise bad_request(f"face_method '{locked.face_method}' のワークフローがありません")
    character_id = new_ulid()
    refs = await _build_references(s, character_id, 1, body.reference_image_ids, body.primary_image_id)
    now = utcnow()
    character = Character(
        id=character_id,
        name=body.name.strip() or DRAFT_NAME,
        tags=[t.strip() for t in body.tags if t.strip()],
        description=body.description,
        status="draft" if body.draft else "active",
        is_synthetic=body.is_synthetic,
        adult_confirmed=body.adult_confirmed,
        current_version=1,
        generation_count=0,
        last_used_at=None,
        created_at=now,
        updated_at=now,
    )
    primary = next((r for r in refs if r.is_primary), refs[0])
    version = CharacterVersion(
        character_id=character_id,
        version=1,
        locked=locked,
        thumbnail_path=primary.image_path,
        note="初版",
        created_at=now,
        references=refs,
    )
    s.db.insert_character(character)
    s.db.insert_version(version)
    return character_detail(character, version, [1])


@router.get("/{character_id}", response_model=CharacterDetail)
async def get_character(character_id: str, s: ServicesDep) -> CharacterDetail:
    character = _character_or_404(s, character_id)
    versions = [v.version for v in s.db.list_versions(character.id)]
    return character_detail(character, _current_version(s, character), versions)


@router.patch("/{character_id}", response_model=CharacterDetail)
async def patch_character(character_id: str, body: CharacterPatchRequest, s: ServicesDep) -> CharacterDetail:
    character = _character_or_404(s, character_id)
    if body.name is not None:
        character.name = body.name.strip()
    if body.tags is not None:
        character.tags = [t.strip() for t in body.tags if t.strip()]
    if body.description is not None:
        character.description = body.description
    s.db.update_character(character)
    versions = [v.version for v in s.db.list_versions(character.id)]
    return character_detail(character, _current_version(s, character), versions)


@router.post("/{character_id}/register", response_model=CharacterDetail)
async def register_character(character_id: str, body: CharacterRegisterRequest, s: ServicesDep) -> CharacterDetail:
    """Finalize a draft (wizard step 4). Both declarations are mandatory."""
    character = _character_or_404(s, character_id)
    if character.status != "draft":
        raise bad_request("このキャラクターは既に登録済みです")
    if not body.is_synthetic:
        raise bad_request(
            "is_synthetic: true（実在の人物ではない架空のキャラクターである）の申告が必須です。"
            "実在人物の顔は使用できません。"
        )
    if not body.adult_confirmed:
        raise bad_request("adult_confirmed: true（成人キャラクターである）の確認が必須です。")
    version = _current_version(s, character)
    if version is None:
        raise bad_request("参照顔がありません")
    if body.locked is not None:
        locked = _apply_patch(version.locked, body.locked)
        if locked.face_method not in s.workflows:
            raise bad_request(f"face_method '{locked.face_method}' のワークフローがありません")
        s.db.update_version_locked(character.id, version.version, locked)
        version.locked = locked
    character.name = body.name.strip()
    character.tags = [t.strip() for t in body.tags if t.strip()]
    character.description = body.description
    character.is_synthetic = True
    character.adult_confirmed = True
    character.status = "active"
    s.db.update_character(character)
    return character_detail(character, version, [v.version for v in s.db.list_versions(character.id)])


@router.delete("/{character_id}", response_model=DeleteResponse)
async def delete_character(character_id: str, s: ServicesDep, delete_images: bool = False) -> DeleteResponse:
    character = _character_or_404(s, character_id)
    deleted_refs = 0
    for version in s.db.list_versions(character.id):
        for ref in version.references:
            if s.storage.remove(ref.image_path):
                deleted_refs += 1
    shutil.rmtree(s.storage.data_dir / "refs" / character.id, ignore_errors=True)
    deleted_images = 0
    if delete_images:
        images, _ = s.db.list_images(character_id=character.id, limit=100000)
        for img in images:
            s.storage.remove(img.path)
            s.storage.remove(Path(img.path).with_suffix(".json"))
            s.storage.remove(img.thumbnail_path)
        s.db.hard_delete_images([img.id for img in images])
        deleted_images = len(images)
    else:
        s.db.detach_images_from_character(character.id)
    s.db.delete_character(character.id)
    return DeleteResponse(deleted=True, deleted_images=deleted_images, deleted_references=deleted_refs)


# ----------------------------------------------------------------------------- versions
@router.get("/{character_id}/versions", response_model=VersionListResponse)
async def list_versions(character_id: str, s: ServicesDep) -> VersionListResponse:
    character = _character_or_404(s, character_id)
    return VersionListResponse(
        current_version=character.current_version,
        items=[version_schema(v) for v in s.db.list_versions(character.id)],
    )


@router.post("/{character_id}/versions", response_model=CharacterVersionSchema, status_code=201)
async def create_version(character_id: str, body: VersionCreateRequest, s: ServicesDep) -> CharacterVersionSchema:
    """Replace the reference face as a NEW version (the previous version is kept and can be rolled back to)."""
    character = _character_or_404(s, character_id)
    versions = s.db.list_versions(character.id)
    current = _current_version(s, character)
    if current is None:
        raise bad_request("現在の版がありません")
    next_version = max(v.version for v in versions) + 1
    locked = _apply_patch(current.locked, body.locked)
    if locked.face_method not in s.workflows:
        raise bad_request(f"face_method '{locked.face_method}' のワークフローがありません")
    refs = await _build_references(s, character.id, next_version, body.reference_image_ids, body.primary_image_id)
    primary = next((r for r in refs if r.is_primary), refs[0])
    version = CharacterVersion(
        character_id=character.id,
        version=next_version,
        locked=locked,
        thumbnail_path=primary.image_path,
        note=body.note,
        created_at=utcnow(),
        references=refs,
    )
    s.db.insert_version(version)
    character.current_version = next_version
    s.db.update_character(character)
    return version_schema(version)


@router.post("/{character_id}/rollback/{version}", response_model=CharacterDetail)
async def rollback(character_id: str, version: int, s: ServicesDep) -> CharacterDetail:
    character = _character_or_404(s, character_id)
    target = s.db.get_version(character.id, version)
    if target is None:
        raise not_found("指定した版がありません")
    character.current_version = version
    s.db.update_character(character)
    return character_detail(character, target, [v.version for v in s.db.list_versions(character.id)])


@router.get("/{character_id}/references/{reference_id}/file")
async def reference_file(character_id: str, reference_id: str, s: ServicesDep) -> FileResponse:
    ref = s.db.get_reference(reference_id)
    if ref is None or ref.character_id != character_id or not Path(ref.image_path).is_file():
        raise not_found("参照顔が見つかりません")
    return FileResponse(ref.image_path, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


# ----------------------------------------------------------------------------- analyze (step 2)
@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(
    s: ServicesDep,
    files: Annotated[list[UploadFile] | None, File()] = None,
    image_ids: Annotated[str | None, Form(description="comma-separated existing image ids (drafts)")] = None,
) -> AnalyzeResponse:
    """Quality / embedding / clustering of candidate reference faces. Uploaded files are stored as `upload` images
    so they can be referenced by id at registration; the analysis itself is not persisted."""
    records: list[ImageRecord] = []
    filenames: list[str] = []
    for upload in files or []:
        data = await upload.read()
        if not data:
            continue
        if len(data) > MAX_UPLOAD_BYTES:
            raise bad_request(
                f"ファイルが大きすぎます（最大 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB）: {upload.filename}"
            )
        image_id = new_ulid()
        path = s.storage.image_path("upload", image_id)
        try:
            width, height = s.storage.save_png(data, path)
        except Exception as exc:
            raise bad_request(f"画像として読めません: {upload.filename}（{exc.__class__.__name__}）") from exc
        thumb = s.storage.thumb_path(image_id)
        s.storage.make_thumbnail(path, thumb)
        record = ImageRecord(
            id=image_id,
            kind="upload",
            character_id=None,
            character_name=None,
            character_version=None,
            job_id=None,
            path=str(path),
            thumbnail_path=str(thumb),
            width=width,
            height=height,
            seed=None,
            params_snapshot={"type": "upload", "filename": upload.filename},
            similarity=None,
            similarity_status=None,
            is_adult=True,
            favorite=False,
            rating=None,
            tags=[],
            created_at=utcnow(),
            deleted_at=None,
        )
        s.db.insert_image(record)
        records.append(record)
        filenames.append(upload.filename or image_id)
    if image_ids:
        ids = [i.strip() for i in image_ids.split(",") if i.strip()]
        existing = s.db.get_images(ids)
        found = {img.id for img in existing}
        missing = [i for i in ids if i not in found]
        if missing:
            raise bad_request(f"画像が見つかりません: {', '.join(missing)}")
        records.extend(existing)
        filenames.extend(f"{img.id}.png" for img in existing)
    if not records:
        raise bad_request("files または image_ids を指定してください")

    analyses = await _analyze_image_records(s, records)
    items: list[AnalyzeItem] = []
    embeddings: list[list[float] | None] = []
    qualities: list[Quality | None] = []
    for index, (record, analysis) in enumerate(zip(records, analyses, strict=True)):
        q = analysis.quality
        usable = q.usable and q.face_count >= 1
        items.append(
            AnalyzeItem(
                index=index,
                filename=filenames[index],
                image_id=record.id,
                face_count=q.face_count,
                quality=QualitySchema(**q.to_dict()) if q.face_count else None,
                warnings=list(q.warnings),
                embedding_id=embedding_id(analysis.embedding) if analysis.embedding else None,
                usable=usable,
                thumb_url=image_thumb_url(record.id),
                file_url=image_file_url(record.id),
            )
        )
        embeddings.append(analysis.embedding)
        qualities.append(q if q.face_count else None)
    matrix = s.face.similarity_matrix(embeddings)
    clusters = s.face.clusters(matrix)
    rec = s.face.recommend(qualities, clusters)
    return AnalyzeResponse(
        items=items,
        similarity_matrix=matrix,
        clusters=clusters,
        recommended_index=rec.index,
        recommend_reason=rec.reason,
        thresholds=s.face.thresholds.to_dict(),
    )


# ----------------------------------------------------------------------------- draft (step 1) / verify (step 3)
@router.post("/draft", response_model=JobAccepted, status_code=202)
async def draft(body: DraftRequest, s: ServicesDep, api_key_id: ApiKeyDep) -> JobAccepted:
    """Generate seed faces with txt2img (no character yet)."""
    validate_dimensions(s, body.width, body.height)
    if not s.workflow_usable("txt2img"):
        raise bad_request(f"txt2img ワークフローが使えません: {'; '.join(s.workflow_issues.get('txt2img', []))}")
    checkpoint = await resolve_checkpoint(s, body.checkpoint)
    assessment = await vram_assessment(
        s, method="txt2img", width=body.width, height=body.height, upscale=1.0, face_detailer=False
    )
    reject_if_dangerous(assessment)
    request = {**body.model_dump(), "checkpoint": checkpoint}
    job = Job(
        id=new_ulid(),
        type="draft",
        status="queued",
        progress=JobProgress(total_images=body.count),
        request=request,
        result=None,
        result_image_ids=[],
        error=None,
        character_id=None,
        api_key_id=api_key_id,
        created_at=utcnow(),
        started_at=None,
        finished_at=None,
    )
    position = s.queue.submit(job)
    return JobAccepted(job_id=job.id, position=position)


@router.post("/{character_id}/verify", response_model=JobAccepted, status_code=202)
async def verify(character_id: str, body: VerifyRequest, s: ServicesDep, api_key_id: ApiKeyDep) -> JobAccepted:
    """Identity verification: len(face_weights) × len(scenes) images with similarity to the primary reference."""
    character = _character_or_404(s, character_id)
    version = _current_version(s, character)
    if version is None or not version.references:
        raise bad_request("参照顔がありません")
    if body.face_method not in s.workflows:
        raise bad_request(f"face_method '{body.face_method}' のワークフローがありません")
    if not s.workflow_usable(body.face_method):
        raise bad_request(
            f"{body.face_method} のワークフローが使えません: {'; '.join(s.workflow_issues.get(body.face_method, []))}"
        )
    for scene_id in body.scenes:
        if s.presets.get_scene(scene_id) is None:
            raise bad_request(f"シーンプリセット '{scene_id}' がありません")
    for weight in body.face_weights:
        if not 0.0 <= weight <= 1.5:
            raise bad_request("face_weights は 0.0〜1.5 の範囲にしてください")
    assessment = await vram_assessment(
        s,
        method=body.face_method,
        width=version.locked.default_width,
        height=version.locked.default_height,
        upscale=1.0,
        face_detailer=True,
    )
    reject_if_dangerous(assessment)
    count = len(body.face_weights) * len(body.scenes)
    job = Job(
        id=new_ulid(),
        type="verify",
        status="queued",
        progress=JobProgress(total_images=count),
        request={
            **body.model_dump(),
            "character_id": character.id,
            "character_version": version.version,
            "count": count,
        },
        result=None,
        result_image_ids=[],
        error=None,
        character_id=character.id,
        api_key_id=api_key_id,
        created_at=utcnow(),
        started_at=None,
        finished_at=None,
    )
    position = s.queue.submit(job)
    return JobAccepted(job_id=job.id, position=position)
