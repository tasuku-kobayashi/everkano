"""Domain records -> API schemas (URL fields are built here)."""

from __future__ import annotations

from typing import Any

from app.models import Character, CharacterVersion, ImageRecord, Job, Reference, iso
from app.schemas import (
    CharacterDetail,
    CharacterStats,
    CharacterSummary,
    CharacterVersionSchema,
    ImageDetail,
    ImageSchema,
    JobProgressSchema,
    JobSchema,
    LockedParamsSchema,
    QualitySchema,
    ReferenceSchema,
)


def image_file_url(image_id: str) -> str:
    return f"/api/images/{image_id}/file"


def image_thumb_url(image_id: str) -> str:
    return f"/api/images/{image_id}/thumb"


def reference_file_url(ref: Reference) -> str:
    return f"/api/characters/{ref.character_id}/references/{ref.id}/file"


def quality_schema(ref_quality: Any) -> QualitySchema:
    return QualitySchema(**ref_quality.to_dict())


def reference_schema(ref: Reference) -> ReferenceSchema:
    return ReferenceSchema(
        id=ref.id,
        position=ref.position,
        source_image_id=ref.source_image_id,
        quality=quality_schema(ref.quality),
        is_primary=ref.is_primary,
        file_url=reference_file_url(ref),
    )


def version_schema(version: CharacterVersion) -> CharacterVersionSchema:
    primary = version.primary
    return CharacterVersionSchema(
        version=version.version,
        locked=LockedParamsSchema(**version.locked.to_dict()),
        note=version.note,
        created_at=iso(version.created_at) or "",
        thumbnail_url=reference_file_url(primary) if primary else "",
        references=[reference_schema(r) for r in version.references],
    )


def character_summary(character: Character, version: CharacterVersion | None) -> CharacterSummary:
    primary = version.primary if version else None
    return CharacterSummary(
        id=character.id,
        name=character.name,
        tags=list(character.tags),
        description=character.description,
        status=character.status,
        is_synthetic=character.is_synthetic,
        adult_confirmed=character.adult_confirmed,
        current_version=character.current_version,
        created_at=iso(character.created_at) or "",
        updated_at=iso(character.updated_at) or "",
        stats=CharacterStats(generations=character.generation_count, last_used=iso(character.last_used_at)),
        thumbnail_url=reference_file_url(primary) if primary else None,
        reference_urls=[reference_file_url(r) for r in version.references] if version else [],
        face_method=version.locked.face_method if version else None,
    )


def character_detail(character: Character, version: CharacterVersion | None, versions: list[int]) -> CharacterDetail:
    summary = character_summary(character, version)
    return CharacterDetail(
        **summary.model_dump(),
        locked=LockedParamsSchema(**version.locked.to_dict()) if version else None,
        references=[reference_schema(r) for r in version.references] if version else [],
        versions=versions,
    )


def image_schema(image: ImageRecord) -> ImageSchema:
    snap = image.params_snapshot or {}
    return ImageSchema(
        id=image.id,
        kind=image.kind,
        character_id=image.character_id,
        character_name=image.character_name,
        character_version=image.character_version,
        job_id=image.job_id,
        width=image.width,
        height=image.height,
        seed=image.seed,
        similarity=image.similarity,
        similarity_status=image.similarity_status,
        is_adult=image.is_adult,
        favorite=image.favorite,
        rating=image.rating,
        tags=list(image.tags),
        created_at=iso(image.created_at) or "",
        file_url=image_file_url(image.id),
        thumb_url=image_thumb_url(image.id),
        face_method=snap.get("face_method"),
        prompt=snap.get("prompt"),
    )


def image_detail(image: ImageRecord) -> ImageDetail:
    return ImageDetail(**image_schema(image).model_dump(), params_snapshot=image.params_snapshot)


def job_schema(job: Job, queue_position: int | None) -> JobSchema:
    return JobSchema(
        id=job.id,
        type=job.type,
        status=job.status,
        progress=JobProgressSchema(**job.progress.to_dict()),
        queue_position=queue_position,
        request=job.request,
        result=job.result,
        result_image_ids=list(job.result_image_ids),
        error=job.error,
        character_id=job.character_id,
        created_at=iso(job.created_at) or "",
        started_at=iso(job.started_at),
        finished_at=iso(job.finished_at),
    )
