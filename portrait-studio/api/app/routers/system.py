"""GET /api/system/vram, POST /api/system/free, GET /api/system/models, GET /api/system/vram-table."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.routers.common import ServicesDep, not_found
from app.schemas import (
    ComplianceResponse,
    FreeResponse,
    ModelsResponse,
    VramResponse,
    VramTableEntry,
    VramTableResponse,
    WorkflowJsonResponse,
)
from app.security import require_api_key

router = APIRouter(prefix="/system", tags=["system"], dependencies=[Depends(require_api_key)])

MODEL_FOLDERS = (
    "checkpoints",
    "loras",
    "controlnet",
    "pulid",
    "ipadapter",
    "insightface",
    "instantid",
    "clip_vision",
    "vae",
    "upscale_models",
    "ultralytics",
)


@router.get("/vram", response_model=VramResponse)
async def vram(s: ServicesDep) -> VramResponse:
    stats = await s.comfy.system_stats()
    return VramResponse(
        gpu=stats.gpu_name,
        total_mb=stats.vram_total_mb,
        free_mb=stats.vram_free_mb,
        used_mb=max(0, stats.vram_total_mb - stats.vram_free_mb),
        torch_total_mb=stats.torch_vram_total_mb,
        torch_free_mb=stats.torch_vram_free_mb,
        torch=stats.pytorch_version,
        comfy_version=stats.comfy_version,
    )


@router.post("/free", response_model=FreeResponse)
async def free(s: ServicesDep) -> FreeResponse:
    before = (await s.comfy.system_stats()).vram_free_mb
    await s.comfy.free(unload_models=True, free_memory=True)
    after = (await s.comfy.system_stats()).vram_free_mb
    return FreeResponse(ok=True, free_mb_before=before, free_mb_after=after)


@router.get("/models", response_model=ModelsResponse)
async def models(s: ServicesDep) -> ModelsResponse:
    folders: dict[str, list[str]] = {}
    available = set(await s.comfy.list_model_folders())
    for folder in MODEL_FOLDERS:
        if available and folder not in available:
            continue
        names = await s.comfy.list_models(folder)
        if names or folder == "checkpoints":
            folders[folder] = names
    if "checkpoints" not in folders:
        folders["checkpoints"] = await s.comfy.checkpoints()
    return ModelsResponse(folders=folders)


@router.get("/compliance", response_model=ComplianceResponse)
async def compliance(s: ServicesDep) -> ComplianceResponse:
    """docs/COMPLIANCE.md so the settings screen can show the legal / prohibited-use notes."""
    path = s.settings.compliance_path
    if not path.is_file():
        raise not_found(f"COMPLIANCE.md がありません: {path}")
    return ComplianceResponse(markdown=path.read_text(encoding="utf-8"), path=str(path))


@router.get("/workflows/{method}", response_model=WorkflowJsonResponse)
async def workflow_json(method: str, s: ServicesDep) -> WorkflowJsonResponse:
    """Raw API-format workflow (for the 上級 panel: view / copy the JSON)."""
    wf = s.workflows.get(method)
    if wf is None:
        raise not_found("ワークフローがありません")
    return WorkflowJsonResponse(method=method, file=wf.path.name, titles=dict(wf.titles), nodes=wf.nodes)


@router.get("/vram-table", response_model=VramTableResponse)
async def vram_table(s: ServicesDep) -> VramTableResponse:
    return VramTableResponse(
        gpu=s.vram.gpu,
        vram_total_mb=s.vram.vram_total_mb,
        measured_at=s.vram.measured_at,
        entries=[VramTableEntry(**e.to_dict()) for e in s.vram.entries],
    )
