"""Pydantic request / response schemas (the OpenAPI document generates `web/src/api/types.ts` from them)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import DEFAULT_NEGATIVE_PROMPT, DEFAULT_PREFIX_PROMPT, FaceMethod

RiskLevel = Literal["low", "medium", "high", "unknown"]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Detail(ApiModel):
    detail: str


# ----------------------------------------------------------------------------- health / system
class WorkflowStatus(ApiModel):
    method: str
    file: str
    ok: bool
    issues: list[str] = Field(default_factory=list)


class HealthResponse(ApiModel):
    ok: bool
    gpu: str | None = None
    vram_total_mb: int | None = None
    vram_free_mb: int | None = None
    torch: str | None = None
    cuda: str | None = None
    comfy_version: str | None = None
    checkpoints: list[str] = Field(default_factory=list)
    face_methods: list[str] = Field(default_factory=list)
    comfy_connected: bool
    face_engine: str
    face_engine_ready: bool
    workflows: list[WorkflowStatus] = Field(default_factory=list)
    version: str
    error: str | None = None


class VramResponse(ApiModel):
    gpu: str | None
    total_mb: int
    free_mb: int
    used_mb: int
    torch_total_mb: int | None = None
    torch_free_mb: int | None = None
    torch: str | None = None
    comfy_version: str | None = None


class ModelsResponse(ApiModel):
    folders: dict[str, list[str]]


class VramTableEntry(ApiModel):
    method: str
    width: int
    height: int
    upscale: float
    face_detailer: bool
    peak_mb: int | None
    measured_at: str | None = None
    note: str | None = None


class VramTableResponse(ApiModel):
    gpu: str | None
    vram_total_mb: int | None
    measured_at: str | None
    checkpoint: str | None = None
    lora: str | None = None
    entries: list[VramTableEntry]


class ComplianceResponse(ApiModel):
    markdown: str
    path: str


class WorkflowJsonResponse(ApiModel):
    method: str
    file: str
    titles: dict[str, str]
    nodes: dict[str, Any]


class FreeResponse(ApiModel):
    ok: bool
    free_mb_before: int | None
    free_mb_after: int | None


# ----------------------------------------------------------------------------- characters
class LockedParamsSchema(ApiModel):
    checkpoint: str = Field(min_length=1)
    face_method: FaceMethod = "pulid"
    face_weight: float = Field(0.8, ge=0.0, le=1.5)
    prefix_prompt: str = DEFAULT_PREFIX_PROMPT
    negative_prompt: str = DEFAULT_NEGATIVE_PROMPT
    default_width: int = Field(832, ge=512, le=2048, multiple_of=8)
    default_height: int = Field(1216, ge=512, le=2048, multiple_of=8)
    face_detailer_denoise: float = Field(0.35, ge=0.0, le=0.49)
    hires_max: float = Field(1.3, ge=1.0, le=2.0)
    lora: str | None = None
    lora_strength: float = Field(0.0, ge=0.0, le=2.0)


class LockedParamsPatch(ApiModel):
    """Partial LockedParams (used for `overrides`, registration adjustments and new versions)."""

    checkpoint: str | None = None
    face_method: FaceMethod | None = None
    face_weight: float | None = Field(None, ge=0.0, le=1.5)
    prefix_prompt: str | None = None
    negative_prompt: str | None = None
    default_width: int | None = Field(None, ge=512, le=2048, multiple_of=8)
    default_height: int | None = Field(None, ge=512, le=2048, multiple_of=8)
    face_detailer_denoise: float | None = Field(None, ge=0.0, le=0.49)
    hires_max: float | None = Field(None, ge=1.0, le=2.0)
    lora: str | None = None
    lora_strength: float | None = Field(None, ge=0.0, le=2.0)


class QualitySchema(ApiModel):
    face_count: int
    face_ratio: float
    det_score: float
    sharpness: float
    yaw: float
    pitch: float
    roll: float
    composite: float
    warnings: list[str]
    usable: bool
    grade: Literal["recommended", "acceptable", "not_recommended", "unusable"]


class ReferenceSchema(ApiModel):
    id: str
    position: int
    source_image_id: str | None
    quality: QualitySchema
    is_primary: bool
    file_url: str


class CharacterVersionSchema(ApiModel):
    version: int
    locked: LockedParamsSchema
    note: str
    created_at: str
    thumbnail_url: str
    references: list[ReferenceSchema]


class CharacterStats(ApiModel):
    generations: int
    last_used: str | None


class CharacterSummary(ApiModel):
    id: str
    name: str
    tags: list[str]
    description: str
    status: Literal["draft", "active"]
    is_synthetic: bool
    adult_confirmed: bool
    current_version: int
    created_at: str
    updated_at: str
    stats: CharacterStats
    thumbnail_url: str | None
    reference_urls: list[str]
    face_method: FaceMethod | None


class CharacterDetail(CharacterSummary):
    locked: LockedParamsSchema | None
    references: list[ReferenceSchema]
    versions: list[int]


class CharacterListResponse(ApiModel):
    items: list[CharacterSummary]


class CharacterCreateRequest(ApiModel):
    """Registration (wizard step 4) or draft creation (`draft: true`, wizard step 3: only used for verification)."""

    name: str = Field("", max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=30)
    description: str = Field("", max_length=2000)
    is_synthetic: bool = False
    adult_confirmed: bool = False
    reference_image_ids: list[str] = Field(default_factory=list, max_length=8)
    primary_image_id: str | None = None
    locked: LockedParamsPatch | None = None
    draft: bool = False


class CharacterRegisterRequest(ApiModel):
    """Finalize a draft character (wizard step 4)."""

    name: str = Field(min_length=1, max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=30)
    description: str = Field("", max_length=2000)
    is_synthetic: bool
    adult_confirmed: bool
    locked: LockedParamsPatch | None = None


class CharacterPatchRequest(ApiModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    tags: list[str] | None = Field(None, max_length=30)
    description: str | None = Field(None, max_length=2000)


class VersionCreateRequest(ApiModel):
    reference_image_ids: list[str] = Field(min_length=1, max_length=8)
    primary_image_id: str | None = None
    note: str = Field("", max_length=500)
    locked: LockedParamsPatch | None = None


class VersionListResponse(ApiModel):
    current_version: int
    items: list[CharacterVersionSchema]


class DeleteResponse(ApiModel):
    deleted: bool
    deleted_images: int = 0
    deleted_references: int = 0


class AnalyzeItem(ApiModel):
    index: int
    filename: str
    image_id: str
    face_count: int
    quality: QualitySchema | None
    warnings: list[str]
    embedding_id: str | None
    usable: bool
    thumb_url: str
    file_url: str


class AnalyzeResponse(ApiModel):
    items: list[AnalyzeItem]
    similarity_matrix: list[list[float | None]]
    clusters: list[list[int]]
    recommended_index: int | None
    recommend_reason: str
    thresholds: dict[str, float]


class DraftRequest(ApiModel):
    prompt: str = Field(min_length=1, max_length=2000)
    count: int = Field(6, ge=1, le=8)
    checkpoint: str | None = None
    seed: int = Field(-1, ge=-1)
    width: int = Field(832, ge=512, le=2048, multiple_of=8)
    height: int = Field(1216, ge=512, le=2048, multiple_of=8)
    negative_prompt: str | None = None
    steps: int | None = Field(None, ge=1, le=100)
    cfg: float | None = Field(None, ge=0.0, le=30.0)


class VerifyRequest(ApiModel):
    face_method: FaceMethod = "pulid"
    face_weights: list[float] = Field(default_factory=lambda: [0.6, 0.8, 1.0], min_length=1, max_length=5)
    scenes: list[str] = Field(
        default_factory=lambda: ["portrait_closeup", "upper_body_cafe", "full_body_street"], min_length=1, max_length=5
    )
    seed: int = Field(-1, ge=-1)
    checkpoint: str | None = None


class JobAccepted(ApiModel):
    job_id: str
    position: int


# ----------------------------------------------------------------------------- generation
class GenerateRequest(ApiModel):
    character_id: str
    prompt: str = Field(min_length=1, max_length=2000)
    negative_prompt: str | None = None
    count: int = Field(4, ge=1, le=8)
    seed: int = Field(-1, ge=-1)
    width: int | None = Field(None, ge=512, le=2048, multiple_of=8)
    height: int | None = Field(None, ge=512, le=2048, multiple_of=8)
    upscale: float | None = Field(None, ge=1.0, le=2.0)
    face_detailer: bool | None = None
    scene_ids: list[str] = Field(default_factory=list, max_length=5)
    steps: int | None = Field(None, ge=1, le=100)
    cfg: float | None = Field(None, ge=0.0, le=30.0)
    sampler_name: str | None = None
    scheduler: str | None = None
    overrides: LockedParamsPatch | None = None
    allow_locked_override: bool = False
    save_as_version: bool = False
    adult_only: bool = False


class PreviewVramRequest(ApiModel):
    method: str = "pulid"
    width: int = Field(832, ge=64, le=8192)
    height: int = Field(1216, ge=64, le=8192)
    count: int = Field(1, ge=1, le=8)
    upscale: float = Field(1.0, ge=1.0, le=4.0)
    face_detailer: bool = True


class PreviewVramResponse(ApiModel):
    estimated_peak_mb: int | None
    free_mb: int | None
    total_mb: int | None
    risk: RiskLevel
    basis: Literal["measured", "bounded", "unknown"]
    advice: str
    would_reject: bool


# ----------------------------------------------------------------------------- jobs
class JobProgressSchema(ApiModel):
    step: int
    total: int
    current: int
    total_images: int
    stage: str


class JobSchema(ApiModel):
    id: str
    type: Literal["generate", "draft", "verify"]
    status: Literal["queued", "running", "done", "error", "canceled"]
    progress: JobProgressSchema
    queue_position: int | None
    request: dict[str, Any]
    result: dict[str, Any] | None
    result_image_ids: list[str]
    error: str | None
    character_id: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None


class JobListResponse(ApiModel):
    items: list[JobSchema]


# ----------------------------------------------------------------------------- images
class ImageSchema(ApiModel):
    id: str
    kind: Literal["generated", "draft", "verify", "upload"]
    character_id: str | None
    character_name: str | None
    character_version: int | None
    job_id: str | None
    width: int
    height: int
    seed: int | None
    similarity: float | None
    similarity_status: Literal["computed", "no_face", "no_reference", "error"] | None
    is_adult: bool
    favorite: bool
    rating: int | None
    tags: list[str]
    created_at: str
    file_url: str
    thumb_url: str
    face_method: str | None
    prompt: str | None


class ImageDetail(ImageSchema):
    params_snapshot: dict[str, Any]


class ImageListResponse(ApiModel):
    items: list[ImageSchema]
    total: int
    limit: int
    offset: int


class ImagePatchRequest(ApiModel):
    favorite: bool | None = None
    rating: int | None = Field(None, ge=0, le=5)
    tags: list[str] | None = Field(None, max_length=30)


class ImageBulkPatchRequest(ApiModel):
    image_ids: list[str] = Field(min_length=1, max_length=500)
    favorite: bool | None = None
    add_tags: list[str] = Field(default_factory=list, max_length=30)
    remove_tags: list[str] = Field(default_factory=list, max_length=30)


class ImageBulkDeleteRequest(ApiModel):
    image_ids: list[str] = Field(min_length=1, max_length=500)


class ImageZipRequest(ApiModel):
    image_ids: list[str] = Field(min_length=1, max_length=500)


class BulkResponse(ApiModel):
    updated: int


class RegenerateRequest(ApiModel):
    keep_seed: bool = True
    count: int = Field(1, ge=1, le=8)


class SimilarityResponse(ApiModel):
    image_id: str
    similarity: float | None
    status: Literal["computed", "no_face", "no_reference", "error"]
    reason: str | None
    reference_id: str | None
    grade: Literal["good", "acceptable", "warning", "unknown"]


# ----------------------------------------------------------------------------- presets
class ScenePresetSchema(ApiModel):
    id: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=80)
    fragments: list[str] = Field(default_factory=list, max_length=20)
    description: str = Field("", max_length=500)
    builtin: bool = False
    verify: bool = False


class ScenePresetListResponse(ApiModel):
    items: list[ScenePresetSchema]


class StylePart(ApiModel):
    id: str
    name: str
    category: str
    fragment: str


class StylePresetResponse(ApiModel):
    prefix_prompt: str
    negative_prompt: str
    parts: list[StylePart]


# ----------------------------------------------------------------------------- audit
class AuditEntry(ApiModel):
    model_config = ConfigDict(extra="allow")

    timestamp: str
    endpoint: str
    job_id: str | None = None
    status: str | None = None


class AuditResponse(ApiModel):
    items: list[dict[str, Any]]
    path: str
    total_lines: int
