"""Job execution: `generate` (existing character), `draft` (seed faces, txt2img) and `verify` (weights × scenes).

Every image is one ComfyUI prompt with batch_size 1. After each image: download, save PNG + thumbnail,
compute ArcFace similarity against the character's primary reference, write the sidecar JSON and the DB row.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.comfy_client import ComfyError, JobCanceled, PromptResult
from app.ids import new_ulid
from app.models import Character, CharacterVersion, ImageKind, ImageRecord, Job, LockedParams, SimilarityStatus, utcnow
from app.presets import compose_prompt
from app.workflow import GenerationParams, WorkflowError, build_prompt

if TYPE_CHECKING:
    from app.container import Services

logger = logging.getLogger("portrait.jobs")

SaveJob = Callable[[Job], None]
PROGRESS_SAVE_INTERVAL_SECONDS = 0.25


class JobFailed(RuntimeError):
    """A job cannot run (message is shown to the user)."""


@dataclass(slots=True)
class ImagePlan:
    kind: ImageKind
    params: GenerationParams
    snapshot: dict[str, Any]
    character: Character | None = None
    version: CharacterVersion | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class JobRunner:
    def __init__(self, services: Services) -> None:
        self.s = services

    # ------------------------------------------------------------------ planning
    def _sampler_values(self, method: str, request: dict[str, Any]) -> dict[str, Any]:
        defaults = self.s.workflows[method].sampler_defaults()
        return {
            "steps": request.get("steps") or defaults["steps"],
            "cfg": request.get("cfg") if request.get("cfg") is not None else defaults["cfg"],
            "sampler_name": request.get("sampler_name") or defaults["sampler_name"],
            "scheduler": request.get("scheduler") or defaults["scheduler"],
        }

    @staticmethod
    def _base_seed(seed: int) -> int:
        return random.randrange(0, 2**32) if seed is None or seed < 0 else int(seed)

    def _scene_fragments(self, scene_ids: list[str]) -> tuple[list[str], list[str]]:
        fragments: list[str] = []
        names: list[str] = []
        for scene_id in scene_ids:
            preset = self.s.presets.get_scene(scene_id)
            if preset is None:
                raise JobFailed(f"シーンプリセット '{scene_id}' がありません")
            fragments.extend(preset.fragments)
            names.append(preset.name)
        return fragments, names

    def plan(self, job: Job) -> list[ImagePlan]:
        if job.type == "draft":
            return self._plan_draft(job)
        if job.type == "verify":
            return self._plan_verify(job)
        return self._plan_generate(job)

    def _plan_draft(self, job: Job) -> list[ImagePlan]:
        req = job.request
        method = "txt2img"
        sampler = self._sampler_values(method, req)
        base_seed = self._base_seed(int(req.get("seed", -1)))
        prefix = str(req.get("prefix_prompt") or LockedParams(checkpoint="").prefix_prompt)
        negative = str(req.get("negative_prompt") or LockedParams(checkpoint="").negative_prompt)
        positive = compose_prompt(prefix, [], str(req["prompt"]))
        plans: list[ImagePlan] = []
        for i in range(int(req.get("count", 1))):
            seed = (base_seed + i) % (2**32)
            params = GenerationParams(
                checkpoint=str(req["checkpoint"]),
                positive=positive,
                negative=negative,
                width=int(req.get("width", 832)),
                height=int(req.get("height", 1216)),
                seed=seed,
                upscale=1.0,
                face_method=None,
                face_detailer=False,
                filename_prefix=f"portrait-studio/draft/{job.id}",
                **sampler,
            )
            snapshot = {
                "job_id": job.id,
                "type": "draft",
                "character_id": None,
                "character_version": None,
                "face_method": None,
                "face_weight": None,
                "checkpoint": params.checkpoint,
                "prompt": req["prompt"],
                "scene_ids": [],
                "prefix_prompt": prefix,
                "positive": positive,
                "negative_prompt": negative,
                "width": params.width,
                "height": params.height,
                "upscale": 1.0,
                "face_detailer": False,
                "face_detailer_denoise": None,
                "seed": seed,
                "index": i,
                "count": int(req.get("count", 1)),
                "workflow_file": self.s.workflows[method].path.name,
                **sampler,
            }
            plans.append(ImagePlan(kind="draft", params=params, snapshot=snapshot, meta={"index": i}))
        return plans

    def _load_character(self, job: Job, *, allow_draft: bool) -> tuple[Character, CharacterVersion]:
        character_id = str(job.request.get("character_id") or job.character_id)
        character = self.s.db.get_character(character_id)
        if character is None:
            raise JobFailed("キャラクターが見つかりません（削除された可能性があります）")
        if character.status == "draft" and not allow_draft:
            raise JobFailed("下書きのキャラクターでは生成できません。先に登録してください。")
        version_no = int(job.request.get("character_version") or character.current_version)
        version = self.s.db.get_version(character.id, version_no)
        if version is None or not version.references:
            raise JobFailed("キャラクターの参照顔がありません")
        return character, version

    def _plan_generate(self, job: Job) -> list[ImagePlan]:
        req = job.request
        character, version = self._load_character(job, allow_draft=False)
        locked = LockedParams.from_dict({**version.locked.to_dict(), **(req.get("effective_overrides") or {})})
        method = locked.face_method
        if method not in self.s.workflows:
            raise JobFailed(f"face_method '{method}' のワークフローがありません")
        sampler = self._sampler_values(method, req)
        scene_ids = [str(s) for s in req.get("scene_ids", [])]
        fragments, scene_names = self._scene_fragments(scene_ids)
        positive = compose_prompt(locked.prefix_prompt, fragments, str(req["prompt"]))
        negative = str(req.get("negative_prompt") or locked.negative_prompt)
        width = int(req.get("width") or locked.default_width)
        height = int(req.get("height") or locked.default_height)
        upscale = float(req.get("upscale") or 1.0)
        face_detailer = bool(req.get("face_detailer", True))
        base_seed = self._base_seed(int(req.get("seed", -1)))
        primary = version.primary
        assert primary is not None  # noqa: S101 - guarded in _load_character
        plans: list[ImagePlan] = []
        count = int(req.get("count", 1))
        for i in range(count):
            seed = (base_seed + i) % (2**32)
            params = GenerationParams(
                checkpoint=locked.checkpoint,
                positive=positive,
                negative=negative,
                width=width,
                height=height,
                seed=seed,
                upscale=upscale,
                hires_denoise=req.get("hires_denoise"),
                face_method=method,
                face_weight=locked.face_weight,
                face_detailer=face_detailer,
                face_detailer_denoise=locked.face_detailer_denoise,
                lora=locked.lora,
                lora_strength=locked.lora_strength,
                filename_prefix=f"portrait-studio/{character.id}/{job.id}",
                **sampler,
            )
            snapshot = {
                "job_id": job.id,
                "type": "generate",
                "character_id": character.id,
                "character_name": character.name,
                "character_version": version.version,
                "reference_id": primary.id,
                "face_method": method,
                "face_weight": locked.face_weight,
                "checkpoint": locked.checkpoint,
                "lora": locked.lora,
                "lora_strength": locked.lora_strength,
                "prompt": req["prompt"],
                "scene_ids": scene_ids,
                "scene_names": scene_names,
                "scene_fragments": fragments,
                "prefix_prompt": locked.prefix_prompt,
                "positive": positive,
                "negative_prompt": negative,
                "width": width,
                "height": height,
                "upscale": upscale,
                "hires_denoise": req.get("hires_denoise"),
                "face_detailer": face_detailer,
                "face_detailer_denoise": locked.face_detailer_denoise,
                "hires_max": locked.hires_max,
                "seed": seed,
                "index": i,
                "count": count,
                "locked_override": bool(req.get("effective_overrides")),
                "overrides": req.get("effective_overrides") or {},
                "regenerate_of": req.get("regenerate_of"),
                "workflow_file": self.s.workflows[method].path.name,
                **sampler,
            }
            plans.append(
                ImagePlan(
                    kind="generated",
                    params=params,
                    snapshot=snapshot,
                    character=character,
                    version=version,
                    meta={"index": i},
                )
            )
        return plans

    def _plan_verify(self, job: Job) -> list[ImagePlan]:
        req = job.request
        character, version = self._load_character(job, allow_draft=True)
        method = str(req.get("face_method", "pulid"))
        if method not in self.s.workflows:
            raise JobFailed(f"face_method '{method}' のワークフローがありません")
        locked = LockedParams.from_dict(version.locked.to_dict())
        checkpoint = str(req.get("checkpoint") or locked.checkpoint)
        sampler = self._sampler_values(method, req)
        base_seed = self._base_seed(int(req.get("seed", -1)))
        weights = [float(w) for w in req.get("face_weights", [0.6, 0.8, 1.0])]
        scenes = [str(s) for s in req.get("scenes", [])]
        primary = version.primary
        assert primary is not None  # noqa: S101
        plans: list[ImagePlan] = []
        index = 0
        for weight in weights:
            for scene_index, scene_id in enumerate(scenes):
                fragments, scene_names = self._scene_fragments([scene_id])
                positive = compose_prompt(locked.prefix_prompt, fragments, "")
                seed = (base_seed + scene_index) % (2**32)  # same seed per scene across weights: comparable
                params = GenerationParams(
                    checkpoint=checkpoint,
                    positive=positive,
                    negative=locked.negative_prompt,
                    width=locked.default_width,
                    height=locked.default_height,
                    seed=seed,
                    upscale=1.0,
                    face_method=method,
                    face_weight=weight,
                    face_detailer=True,
                    face_detailer_denoise=locked.face_detailer_denoise,
                    lora=locked.lora,
                    lora_strength=locked.lora_strength,
                    filename_prefix=f"portrait-studio/{character.id}/verify/{job.id}",
                    **sampler,
                )
                snapshot = {
                    "job_id": job.id,
                    "type": "verify",
                    "character_id": character.id,
                    "character_name": character.name,
                    "character_version": version.version,
                    "reference_id": primary.id,
                    "face_method": method,
                    "face_weight": weight,
                    "checkpoint": checkpoint,
                    "lora": locked.lora,
                    "lora_strength": locked.lora_strength,
                    "prompt": "",
                    "scene_ids": [scene_id],
                    "scene_names": scene_names,
                    "scene_fragments": fragments,
                    "prefix_prompt": locked.prefix_prompt,
                    "positive": positive,
                    "negative_prompt": locked.negative_prompt,
                    "width": locked.default_width,
                    "height": locked.default_height,
                    "upscale": 1.0,
                    "face_detailer": True,
                    "face_detailer_denoise": locked.face_detailer_denoise,
                    "seed": seed,
                    "index": index,
                    "count": len(weights) * len(scenes),
                    "workflow_file": self.s.workflows[method].path.name,
                    **sampler,
                }
                plans.append(
                    ImagePlan(
                        kind="verify",
                        params=params,
                        snapshot=snapshot,
                        character=character,
                        version=version,
                        meta={"index": index, "scene": scene_id, "face_weight": weight},
                    )
                )
                index += 1
        return plans

    # ------------------------------------------------------------------ execution
    async def _upload_reference(self, version: CharacterVersion) -> str:
        primary = version.primary
        assert primary is not None  # noqa: S101
        data = await asyncio.to_thread(Path(primary.image_path).read_bytes)
        name = f"{version.character_id}_v{version.version}_{primary.id}.png"
        return await self.s.comfy.upload_image(data, name, subfolder=self.s.settings.comfy_ref_subfolder)

    async def run(self, job: Job, cancel: asyncio.Event, save: SaveJob) -> None:
        started = time.monotonic()
        plans = self.plan(job)
        job.progress.total_images = len(plans)
        job.progress.current = 0
        job.progress.stage = "準備中"
        save(job)
        ref_image: str | None = None
        needs_ref = any(p.version is not None for p in plans)
        if needs_ref:
            version = next(p.version for p in plans if p.version is not None)
            job.progress.stage = "参照顔をアップロード"
            save(job)
            ref_image = await self._upload_reference(version)
        results: list[dict[str, Any]] = []
        for i, plan in enumerate(plans):
            if cancel.is_set():
                raise JobCanceled
            plan.params.ref_image = ref_image
            job.progress.current = i + 1
            job.progress.step = 0
            job.progress.total = 0
            job.progress.stage = f"{i + 1}/{len(plans)} 枚目を生成中"
            save(job)
            workflow = self.s.workflows[plan.params.face_method or "txt2img"]
            try:
                prompt = build_prompt(workflow, plan.params)
            except WorkflowError as exc:
                raise JobFailed(str(exc)) from exc

            last_saved = 0.0

            def on_progress(value: int, maximum: int, _job: Job = job) -> None:
                nonlocal last_saved
                _job.progress.step = value
                _job.progress.total = maximum
                # one sqlite transaction per WebSocket frame would fsync on every sampler step; 4 Hz is plenty
                if value >= maximum or time.monotonic() - last_saved >= PROGRESS_SAVE_INTERVAL_SECONDS:
                    last_saved = time.monotonic()
                    save(_job)

            result = await self.s.comfy.run_prompt(
                prompt, on_progress=on_progress, cancel=cancel, timeout=self.s.settings.comfy_generation_timeout_seconds
            )
            data = await self.s.comfy.view(result.images[0])
            image = await asyncio.to_thread(self._persist_image, job, plan, data, result)
            job.result_image_ids.append(image.id)
            results.append(
                {
                    "image_id": image.id,
                    "seed": image.seed,
                    "similarity": image.similarity,
                    "similarity_status": image.similarity_status,
                    **plan.meta,
                }
            )
            job.result = self._summarize(job, results)
            save(job)
        if job.type != "draft" and plans and plans[0].character is not None:
            self.s.db.record_generation(plans[0].character.id, len(plans))
        job.result = self._summarize(job, results)
        job.result["duration_ms"] = int((time.monotonic() - started) * 1000)

    @staticmethod
    def _summarize(job: Job, results: list[dict[str, Any]]) -> dict[str, Any]:
        summary: dict[str, Any] = {"items": results}
        if job.type == "verify":
            by_weight: dict[str, list[float]] = {}
            for r in results:
                if r.get("similarity") is not None:
                    by_weight.setdefault(format(float(r["face_weight"]), "g"), []).append(float(r["similarity"]))
            summary["by_weight"] = {k: round(sum(v) / len(v), 4) for k, v in by_weight.items() if v}
            best = max(summary["by_weight"].items(), key=lambda kv: kv[1], default=None)
            summary["best_weight"] = float(best[0]) if best else None
        return summary

    def _persist_image(self, job: Job, plan: ImagePlan, data: bytes, result: PromptResult) -> ImageRecord:
        image_id = new_ulid()
        path = self.s.storage.image_path(plan.kind, image_id)
        width, height = self.s.storage.save_png(data, path)
        thumb = self.s.storage.thumb_path(image_id)
        self.s.storage.make_thumbnail(path, thumb)
        similarity: float | None = None
        status: SimilarityStatus = "no_reference"
        if plan.version is not None and plan.version.primary is not None:
            try:
                analysis = self.s.face.analyze_path(path)
                if analysis.embedding is None:
                    status = "no_face"
                else:
                    from app.face import cosine_similarity  # noqa: PLC0415

                    similarity = round(cosine_similarity(analysis.embedding, plan.version.primary.embedding), 4)
                    status = "computed"
            except Exception as exc:
                logger.warning("similarity failed for %s: %s", image_id, exc)
                status = "error"
        snapshot = {
            **plan.snapshot,
            "image_id": image_id,
            "comfy_prompt_id": result.prompt_id,
            "comfy_output": result.images[0].filename,
            "duration_ms": result.duration_ms,
            "generated_at": utcnow().isoformat(),
        }
        record = ImageRecord(
            id=image_id,
            kind=plan.kind,
            character_id=plan.character.id if plan.character else None,
            character_name=plan.character.name if plan.character else None,
            character_version=plan.version.version if plan.version else None,
            job_id=job.id,
            path=str(path),
            thumbnail_path=str(thumb),
            width=width,
            height=height,
            seed=plan.params.seed,
            params_snapshot=snapshot,
            similarity=similarity,
            similarity_status=status,
            is_adult=True,
            favorite=False,
            rating=None,
            tags=[],
            created_at=utcnow(),
            deleted_at=None,
        )
        self.s.db.insert_image(record)
        self.s.audit.write_sidecar(
            path,
            {
                **snapshot,
                "similarity": similarity,
                "similarity_status": status,
                "path": str(path),
                "api_key_id": job.api_key_id,
                "endpoint": ENDPOINT_BY_TYPE.get(job.type, "/api/generate"),
            },
        )
        return record

    # ------------------------------------------------------------------ audit
    def write_audit(self, job: Job) -> None:
        req = job.request
        images = self.s.db.get_images(job.result_image_ids) if job.result_image_ids else []
        first = images[0].params_snapshot if images else {}
        entry = {
            "timestamp": utcnow().isoformat(),
            "api_key_id": job.api_key_id,
            "endpoint": ENDPOINT_BY_TYPE.get(job.type, "/api/generate"),
            "job_id": job.id,
            "job_type": job.type,
            "status": job.status,
            "character_id": job.character_id,
            "character_version": first.get("character_version") or req.get("character_version"),
            "prompt": req.get("prompt", ""),
            "negative_prompt": first.get("negative_prompt") or req.get("negative_prompt"),
            "seed": req.get("seed"),
            "seeds": [img.seed for img in images],
            "face_method": first.get("face_method") or req.get("face_method"),
            "face_weight": first.get("face_weight") or req.get("face_weights"),
            "checkpoint": first.get("checkpoint") or req.get("checkpoint"),
            "width": first.get("width") or req.get("width"),
            "height": first.get("height") or req.get("height"),
            "count": req.get("count") or len(images),
            "similarity_scores": [img.similarity for img in images],
            "output_files": [img.path for img in images],
            "duration_ms": (job.result or {}).get("duration_ms") if job.result else None,
            "error": job.error,
        }
        self.s.audit.append(entry)


ENDPOINT_BY_TYPE: dict[str, str] = {
    "generate": "/api/generate",
    "draft": "/api/characters/draft",
    "verify": "/api/characters/{id}/verify",
}


__all__ = ["ComfyError", "ImagePlan", "JobFailed", "JobRunner"]
