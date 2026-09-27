"""POST /api/generate and POST /api/generate/preview-vram (+ the job builder shared with regenerate)."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends

from app.container import Services
from app.ids import new_ulid
from app.models import (
    IDENTITY_LOCKED_KEYS,
    Character,
    CharacterVersion,
    Job,
    JobProgress,
    LockedParams,
    Reference,
    utcnow,
)
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


@dataclass(slots=True)
class GenerationSpec:
    """Normalized generation inputs — built from a GenerateRequest or from an image's params_snapshot."""

    prompt: str
    count: int
    seed: int = -1
    negative_prompt: str | None = None
    width: int | None = None
    height: int | None = None
    upscale: float | None = None
    face_detailer: bool | None = None
    scene_ids: list[str] = field(default_factory=list)
    steps: int | None = None
    cfg: float | None = None
    sampler_name: str | None = None
    scheduler: str | None = None
    hires_denoise: float | None = None
    overrides: dict[str, Any] = field(default_factory=dict)
    allow_locked_override: bool = False
    save_as_version: bool = False
    regenerate_of: str | None = None


def _copy_references(s: Services, version: CharacterVersion, next_version: int) -> list[Reference]:
    ref_dir = s.storage.ref_dir(version.character_id, next_version)
    refs: list[Reference] = []
    for ref in version.references:
        new_id = new_ulid()
        dest = ref_dir / f"{new_id}.png"
        shutil.copyfile(ref.image_path, dest)
        refs.append(
            Reference(
                id=new_id,
                character_id=version.character_id,
                version=next_version,
                position=ref.position,
                image_path=str(dest),
                source_image_id=ref.source_image_id,
                embedding=list(ref.embedding),
                quality=ref.quality,
                is_primary=ref.is_primary,
            )
        )
    return refs


async def _new_version_from_overrides(
    s: Services, character: Character, version: CharacterVersion, locked: LockedParams
) -> CharacterVersion:
    """`save_as_version`: keep the same references, store the overridden locked params as a new version."""
    next_version = max(v.version for v in s.db.list_versions(character.id)) + 1
    refs = await asyncio.to_thread(_copy_references, s, version, next_version)
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
    s.db.set_current_version(character.id, next_version)
    character.current_version = next_version
    return new_version


async def prepare_generate_job(
    s: Services, *, character: Character, version: CharacterVersion, spec: GenerationSpec, api_key_id: str
) -> Job:
    """Validate a generation against the character's locked params / VRAM table and build the queued Job.

    Shared by POST /api/generate and POST /api/images/{id}/regenerate so both enforce the same guards.
    """
    if character.status == "draft":
        raise bad_request("下書きのキャラクターでは生成できません。先に登録（Step 4）を完了してください。")
    if not version.references:
        raise bad_request("キャラクターの参照顔がありません")
    locked = version.locked
    overrides = {k: v for k, v in spec.overrides.items() if v is not None}
    changed = {k: v for k, v in overrides.items() if k in IDENTITY_LOCKED_KEYS and v != getattr(locked, k)}
    if changed and not spec.allow_locked_override:
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
    if not 1 <= spec.count <= s.settings.max_count_per_job:
        raise bad_request(f"count は 1〜{s.settings.max_count_per_job} の範囲にしてください")

    width = spec.width or effective.default_width
    height = spec.height or effective.default_height
    validate_dimensions(s, width, height)
    upscale = spec.upscale if spec.upscale is not None else 1.0
    if upscale > effective.hires_max + 1e-9:
        raise bad_request(f"upscale {upscale:g} はこのキャラの hires_max {effective.hires_max:g} を超えています")
    face_detailer = True if spec.face_detailer is None else spec.face_detailer
    for scene_id in spec.scene_ids:
        if s.presets.get_scene(scene_id) is None:
            raise bad_request(f"シーンプリセット '{scene_id}' がありません")

    assessment = await vram_assessment(
        s,
        method=effective.face_method,
        width=width,
        height=height,
        upscale=upscale,
        face_detailer=face_detailer,
        checkpoint=effective.checkpoint,
        lora=effective.lora,
    )
    reject_if_dangerous(assessment)

    if changed and spec.save_as_version:
        version = await _new_version_from_overrides(s, character, version, effective)
        changed = {}

    request: dict[str, Any] = {
        "character_id": character.id,
        "character_version": version.version,
        "prompt": spec.prompt,
        "negative_prompt": spec.negative_prompt,
        "count": spec.count,
        "seed": spec.seed,
        "width": width,
        "height": height,
        "upscale": upscale,
        "face_detailer": face_detailer,
        "scene_ids": list(spec.scene_ids),
        "steps": spec.steps,
        "cfg": spec.cfg,
        "sampler_name": spec.sampler_name,
        "scheduler": spec.scheduler,
        "hires_denoise": spec.hires_denoise,
        "overrides": overrides,
        "effective_overrides": changed,
        "allow_locked_override": spec.allow_locked_override,
        "save_as_version": spec.save_as_version,
        "adult_only": True,
        "regenerate_of": spec.regenerate_of,
        "vram_estimate_mb": assessment.estimated_peak_mb,
    }
    return Job(
        id=new_ulid(),
        type="generate",
        status="queued",
        progress=JobProgress(total_images=spec.count),
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


@router.post("", response_model=JobAccepted, status_code=202)
async def generate(body: GenerateRequest, s: ServicesDep, api_key_id: ApiKeyDep) -> JobAccepted:
    if not body.adult_only:
        raise bad_request("adult_only: true が必須です（成人キャラクター限定の生成であることを明示してください）")
    character = s.db.get_character(body.character_id)
    if character is None:
        raise not_found("キャラクターが見つかりません")
    version = s.db.get_version(character.id, character.current_version)
    if version is None:
        raise bad_request("キャラクターの参照顔がありません")
    spec = GenerationSpec(
        prompt=body.prompt,
        count=body.count,
        seed=body.seed,
        negative_prompt=body.negative_prompt,
        width=body.width,
        height=body.height,
        upscale=body.upscale,
        face_detailer=body.face_detailer,
        scene_ids=list(body.scene_ids),
        steps=body.steps,
        cfg=body.cfg,
        sampler_name=body.sampler_name,
        scheduler=body.scheduler,
        overrides=body.overrides.model_dump(exclude_none=True) if body.overrides else {},
        allow_locked_override=body.allow_locked_override,
        save_as_version=body.save_as_version,
    )
    job = await prepare_generate_job(s, character=character, version=version, spec=spec, api_key_id=api_key_id)
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
