"""POST /api/generate and POST /api/generate/preview-vram."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.ids import new_ulid
from app.models import IDENTITY_LOCKED_KEYS, Character, CharacterVersion, Job, JobProgress, LockedParams, utcnow
from app.routers.common import (
    ServicesDep,
    bad_request,
    not_found,
    reject_if_dangerous,
    validate_dimensions,
    vram_assessment,
)
from app.schemas import GenerateRequest, JobAccepted, PreviewVramRequest, PreviewVramResponse
from app.security import ApiKeyDep, require_api_key

router = APIRouter(prefix="/generate", tags=["generate"], dependencies=[Depends(require_api_key)])

LOCKED_OVERRIDE_MESSAGE = (
    "この操作はキャラの同一性を変えます（{keys}）。変更すると以降の生成は別人になる可能性があります。"
    "続行するには allow_locked_override: true を明示してください。"
)


def _new_version_from_overrides(
    s: Any, character: Character, version: CharacterVersion, locked: LockedParams
) -> CharacterVersion:
    """`save_as_version`: keep the same references, store the overridden locked params as a new version."""
    import shutil  # noqa: PLC0415

    from app.models import Reference  # noqa: PLC0415

    next_version = max(v.version for v in s.db.list_versions(character.id)) + 1
    ref_dir = s.storage.ref_dir(character.id, next_version)
    refs: list[Reference] = []
    for ref in version.references:
        new_id = new_ulid()
        dest = ref_dir / f"{new_id}.png"
        shutil.copyfile(ref.image_path, dest)
        refs.append(
            Reference(
                id=new_id,
                character_id=character.id,
                version=next_version,
                position=ref.position,
                image_path=str(dest),
                source_image_id=ref.source_image_id,
                embedding=list(ref.embedding),
                quality=ref.quality,
                is_primary=ref.is_primary,
            )
        )
    new_version = CharacterVersion(
        character_id=character.id,
        version=next_version,
        locked=locked,
        thumbnail_path=next((r.image_path for r in refs if r.is_primary), refs[0].image_path),
        note="生成時の設定変更を新版として保存",
        created_at=utcnow(),
        references=refs,
    )
    s.db.insert_version(new_version)
    character.current_version = next_version
    s.db.update_character(character)
    return new_version


@router.post("", response_model=JobAccepted, status_code=202)
async def generate(body: GenerateRequest, s: ServicesDep, api_key_id: ApiKeyDep) -> JobAccepted:
    if not body.adult_only:
        raise bad_request("adult_only: true が必須です（成人キャラクター限定の生成であることを明示してください）")
    character = s.db.get_character(body.character_id)
    if character is None:
        raise not_found("キャラクターが見つかりません")
    if character.status == "draft":
        raise bad_request("下書きのキャラクターでは生成できません。先に登録（Step 4）を完了してください。")
    version = s.db.get_version(character.id, character.current_version)
    if version is None or not version.references:
        raise bad_request("キャラクターの参照顔がありません")
    locked = version.locked

    overrides = body.overrides.model_dump(exclude_none=True) if body.overrides else {}
    changed = {k: v for k, v in overrides.items() if k in IDENTITY_LOCKED_KEYS and v != getattr(locked, k)}
    if changed and not body.allow_locked_override:
        raise bad_request(LOCKED_OVERRIDE_MESSAGE.format(keys=", ".join(sorted(changed))))
    effective = LockedParams.from_dict({**locked.to_dict(), **overrides})
    if effective.face_method not in s.workflows:
        raise bad_request(f"face_method '{effective.face_method}' のワークフローがありません")
    if not s.workflow_usable(effective.face_method):
        raise bad_request(
            f"{effective.face_method} のワークフローが使えません: "
            f"{'; '.join(s.workflow_issues.get(effective.face_method, []))}"
        )
    if effective.face_detailer_denoise >= s.settings.face_detailer_denoise_max:
        raise bad_request(
            f"face_detailer_denoise は {s.settings.face_detailer_denoise_max} 未満にしてください（顔が別人化します）"
        )

    width = body.width or effective.default_width
    height = body.height or effective.default_height
    validate_dimensions(s, width, height)
    upscale = body.upscale if body.upscale is not None else 1.0
    if upscale > effective.hires_max + 1e-9:
        raise bad_request(f"upscale {upscale:g} はこのキャラの hires_max {effective.hires_max:g} を超えています")
    face_detailer = True if body.face_detailer is None else body.face_detailer
    for scene_id in body.scene_ids:
        if s.presets.get_scene(scene_id) is None:
            raise bad_request(f"シーンプリセット '{scene_id}' がありません")

    assessment = await vram_assessment(
        s, method=effective.face_method, width=width, height=height, upscale=upscale, face_detailer=face_detailer
    )
    reject_if_dangerous(assessment)

    if changed and body.save_as_version:
        version = _new_version_from_overrides(s, character, version, effective)
        changed = {}

    request = {
        **body.model_dump(),
        "character_version": version.version,
        "effective_overrides": changed,
        "width": width,
        "height": height,
        "upscale": upscale,
        "face_detailer": face_detailer,
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
    position = s.queue.submit(job)
    return JobAccepted(job_id=job.id, position=position)


@router.post("/preview-vram", response_model=PreviewVramResponse)
async def preview_vram(body: PreviewVramRequest, s: ServicesDep) -> PreviewVramResponse:
    a = await vram_assessment(
        s,
        method=body.method,
        width=body.width,
        height=body.height,
        upscale=body.upscale,
        face_detailer=body.face_detailer,
    )
    return PreviewVramResponse(
        estimated_peak_mb=a.estimated_peak_mb,
        free_mb=a.free_mb,
        total_mb=a.total_mb,
        risk=a.risk,
        basis=a.basis,
        advice=a.advice,
        would_reject=a.would_reject,
    )
