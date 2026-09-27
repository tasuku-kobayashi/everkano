"""Domain records stored in SQLite (see app/db.py). Plain dataclasses; API shapes live in app/schemas.py."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

FaceMethod = Literal["pulid", "faceid", "instantid"]
CharacterStatus = Literal["draft", "active"]
JobType = Literal["generate", "draft", "verify"]
JobStatus = Literal["queued", "running", "done", "error", "canceled"]
ImageKind = Literal["generated", "draft", "verify", "upload"]
SimilarityStatus = Literal["computed", "no_face", "no_reference", "error"]

FACE_METHODS: tuple[FaceMethod, ...] = ("pulid", "faceid", "instantid")

# Fixed prefix that keeps "photoreal Japanese adult" stable across prompts (see README: prompt design).
DEFAULT_PREFIX_PROMPT = (
    "photorealistic, RAW photo, realistic, japanese, asian, natural skin texture, "
    "visible skin pores, film grain, 35mm photograph, soft natural lighting, "
    "detailed eyes, sharp focus, professional photography"
)
DEFAULT_NEGATIVE_PROMPT = (
    "anime, cartoon, illustration, 3d render, cgi, painting, drawing, plastic skin, "
    "airbrushed, doll, oversmooth, deformed anatomy, bad hands, extra digits, "
    "fewer digits, watermark, signature, text, jpeg artifacts, lowres, blurry"
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass(slots=True)
class LockedParams:
    """Parameters that define the character's identity. Saved with the character and never edited in place."""

    checkpoint: str
    face_method: FaceMethod = "pulid"
    face_weight: float = 0.8
    prefix_prompt: str = DEFAULT_PREFIX_PROMPT
    negative_prompt: str = DEFAULT_NEGATIVE_PROMPT
    default_width: int = 832
    default_height: int = 1216
    face_detailer_denoise: float = 0.35
    hires_max: float = 1.3
    lora: str | None = None
    lora_strength: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint": self.checkpoint,
            "face_method": self.face_method,
            "face_weight": self.face_weight,
            "prefix_prompt": self.prefix_prompt,
            "negative_prompt": self.negative_prompt,
            "default_width": self.default_width,
            "default_height": self.default_height,
            "face_detailer_denoise": self.face_detailer_denoise,
            "hires_max": self.hires_max,
            "lora": self.lora,
            "lora_strength": self.lora_strength,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LockedParams:
        return cls(
            checkpoint=str(data["checkpoint"]),
            face_method=data.get("face_method", "pulid"),
            face_weight=float(data.get("face_weight", 0.8)),
            prefix_prompt=str(data.get("prefix_prompt", DEFAULT_PREFIX_PROMPT)),
            negative_prompt=str(data.get("negative_prompt", DEFAULT_NEGATIVE_PROMPT)),
            default_width=int(data.get("default_width", 832)),
            default_height=int(data.get("default_height", 1216)),
            face_detailer_denoise=float(data.get("face_detailer_denoise", 0.35)),
            hires_max=float(data.get("hires_max", 1.3)),
            lora=data.get("lora"),
            lora_strength=float(data.get("lora_strength", 0.0)),
        )


# Keys of LockedParams that change the character's identity when overridden at generation time.
IDENTITY_LOCKED_KEYS: frozenset[str] = frozenset(
    {
        "checkpoint",
        "face_method",
        "face_weight",
        "prefix_prompt",
        "face_detailer_denoise",
        "hires_max",
        "lora",
        "lora_strength",
    }
)


@dataclass(slots=True)
class Quality:
    face_count: int
    face_ratio: float
    det_score: float
    sharpness: float
    yaw: float
    pitch: float
    roll: float
    composite: float
    warnings: list[str] = field(default_factory=list)
    usable: bool = True
    grade: Literal["recommended", "acceptable", "not_recommended", "unusable"] = "acceptable"

    def to_dict(self) -> dict[str, Any]:
        return {
            "face_count": self.face_count,
            "face_ratio": self.face_ratio,
            "det_score": self.det_score,
            "sharpness": self.sharpness,
            "yaw": self.yaw,
            "pitch": self.pitch,
            "roll": self.roll,
            "composite": self.composite,
            "warnings": list(self.warnings),
            "usable": self.usable,
            "grade": self.grade,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Quality:
        return cls(
            face_count=int(data.get("face_count", 1)),
            face_ratio=float(data.get("face_ratio", 0.0)),
            det_score=float(data.get("det_score", 0.0)),
            sharpness=float(data.get("sharpness", 0.0)),
            yaw=float(data.get("yaw", 0.0)),
            pitch=float(data.get("pitch", 0.0)),
            roll=float(data.get("roll", 0.0)),
            composite=float(data.get("composite", 0.0)),
            warnings=list(data.get("warnings", [])),
            usable=bool(data.get("usable", True)),
            grade=data.get("grade", "acceptable"),
        )


@dataclass(slots=True)
class Reference:
    id: str
    character_id: str
    version: int
    position: int
    image_path: str
    source_image_id: str | None
    embedding: list[float]
    quality: Quality
    is_primary: bool


@dataclass(slots=True)
class CharacterVersion:
    character_id: str
    version: int
    locked: LockedParams
    thumbnail_path: str
    note: str
    created_at: datetime
    references: list[Reference] = field(default_factory=list)

    @property
    def primary(self) -> Reference | None:
        for ref in self.references:
            if ref.is_primary:
                return ref
        return self.references[0] if self.references else None


@dataclass(slots=True)
class Character:
    id: str
    name: str
    tags: list[str]
    description: str
    status: CharacterStatus
    is_synthetic: bool
    adult_confirmed: bool
    current_version: int
    generation_count: int
    last_used_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class ImageRecord:
    id: str
    kind: ImageKind
    character_id: str | None
    character_name: str | None
    character_version: int | None
    job_id: str | None
    path: str
    thumbnail_path: str
    width: int
    height: int
    seed: int | None
    params_snapshot: dict[str, Any]
    similarity: float | None
    similarity_status: SimilarityStatus | None
    is_adult: bool
    favorite: bool
    rating: int | None
    tags: list[str]
    created_at: datetime
    deleted_at: datetime | None


@dataclass(slots=True)
class JobProgress:
    step: int = 0
    total: int = 0
    current: int = 0
    total_images: int = 0
    stage: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "total": self.total,
            "current": self.current,
            "total_images": self.total_images,
            "stage": self.stage,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobProgress:
        return cls(
            step=int(data.get("step", 0)),
            total=int(data.get("total", 0)),
            current=int(data.get("current", 0)),
            total_images=int(data.get("total_images", 0)),
            stage=str(data.get("stage", "")),
        )


@dataclass(slots=True)
class Job:
    id: str
    type: JobType
    status: JobStatus
    progress: JobProgress
    request: dict[str, Any]
    result: dict[str, Any] | None
    result_image_ids: list[str]
    error: str | None
    character_id: str | None
    api_key_id: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(slots=True)
class ScenePreset:
    id: str
    name: str
    fragments: list[str]
    description: str = ""
    builtin: bool = True
    verify: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "fragments": list(self.fragments),
            "description": self.description,
            "builtin": self.builtin,
            "verify": self.verify,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScenePreset:
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            fragments=[str(f) for f in data.get("fragments", [])],
            description=str(data.get("description", "")),
            builtin=bool(data.get("builtin", False)),
            verify=bool(data.get("verify", False)),
        )
