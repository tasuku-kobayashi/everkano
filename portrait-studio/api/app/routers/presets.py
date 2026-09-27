"""Scene presets (GET/POST/DELETE /api/presets/scenes) and style parts (GET /api/presets/styles)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.models import ScenePreset
from app.presets import load_style_parts
from app.routers.common import ServicesDep, not_found
from app.schemas import DeleteResponse, ScenePresetListResponse, ScenePresetSchema, StylePart, StylePresetResponse
from app.security import require_api_key

router = APIRouter(prefix="/presets", tags=["presets"], dependencies=[Depends(require_api_key)])


def _schema(p: ScenePreset) -> ScenePresetSchema:
    return ScenePresetSchema(**p.to_dict())


@router.get("/scenes", response_model=ScenePresetListResponse)
async def list_scenes(s: ServicesDep) -> ScenePresetListResponse:
    return ScenePresetListResponse(items=[_schema(p) for p in s.presets.list_scenes()])


@router.post("/scenes", response_model=ScenePresetSchema)
async def upsert_scene(body: ScenePresetSchema, s: ServicesDep) -> ScenePresetSchema:
    preset = s.presets.upsert_scene(ScenePreset.from_dict(body.model_dump()))
    return _schema(preset)


@router.delete("/scenes/{scene_id}", response_model=DeleteResponse)
async def delete_scene(scene_id: str, s: ServicesDep) -> DeleteResponse:
    if not s.presets.delete_scene(scene_id):
        raise not_found("シーンプリセットが見つかりません")
    return DeleteResponse(deleted=True)


@router.get("/styles", response_model=StylePresetResponse)
async def styles() -> StylePresetResponse:
    data = load_style_parts()
    return StylePresetResponse(
        prefix_prompt=data["prefix_prompt"],
        negative_prompt=data["negative_prompt"],
        parts=[StylePart(**p) for p in data["parts"]],
    )
