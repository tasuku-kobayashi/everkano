"""Face detection / ArcFace embedding / reference quality / similarity (insightface antelopev2 on CPU).

Quality is only what can be measured:
  face_count  insightface detections (0 = unusable, >=2 = warning)
  face_ratio  bbox area / image area                       guideline >= QUALITY_FACE_RATIO_MIN (0.08)
  det_score   detector confidence                          guideline >= QUALITY_DET_SCORE_MIN (0.6)
  sharpness   Laplacian variance of the face crop (normalized to 224 px wide)   guideline >= QUALITY_SHARPNESS_MIN
  yaw/pitch/roll  from the 3D landmark model (degrees)     guideline abs(yaw) <= QUALITY_YAW_MAX_DEG (15)
  composite   weighted combination (weights below), 0..1, used for sorting / recommendation.

`onnxruntime` (CPU build) only. The engine is loaded lazily in a worker thread (~7 s on first use).
A `MockFaceEngine` (FACE_ENGINE=mock) exists for tests: it reads faces from magenta marker rectangles.
"""

from __future__ import annotations

import hashlib
import io
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageOps

from app.config import Settings
from app.models import Quality

logger = logging.getLogger("portrait.face")

EMBEDDING_DIM = 512
SimilarityGrade = Literal["good", "acceptable", "warning", "unknown"]

# composite = sum(weight * sub_score); sub-scores are clipped to 0..1 (see _quality below)
COMPOSITE_WEIGHTS: dict[str, float] = {"frontal": 0.35, "sharpness": 0.25, "face_ratio": 0.25, "det_score": 0.15}
FACE_RATIO_FULL_SCORE = 0.16  # face_ratio at which the size sub-score reaches 1.0 (twice the 0.08 guideline)
SHARPNESS_FULL_SCORE = 300.0  # Laplacian variance (224 px crop) at which the sharpness sub-score reaches 1.0
FRONTAL_FULL_DEG = 45.0  # yaw / pitch at which the frontal sub-score reaches 0
ROLL_FULL_DEG = 60.0
CROP_WIDTH = 224

WARNING_TEXT: dict[str, str] = {
    "no_face": "顔が検出できません（使用不可）",
    "multiple_faces": "複数の顔が写っています（最も大きい顔で評価）",
    "face_too_small": "顔が小さすぎます（顔の面積比が目安未満）",
    "low_det_score": "検出信頼度が低い",
    "not_frontal": "横顔・傾きが大きい（正面度が目安未満）",
    "blurry": "ぼけています（シャープネスが目安未満）",
    "pose_unavailable": "顔の向きを推定できません",
}


@dataclass(slots=True)
class DetectedFace:
    bbox: tuple[float, float, float, float]
    det_score: float
    embedding: NDArray[np.float32]
    yaw: float | None
    pitch: float | None
    roll: float | None

    @property
    def area(self) -> float:
        x1, y1, x2, y2 = self.bbox
        return max(0.0, x2 - x1) * max(0.0, y2 - y1)


@dataclass(slots=True)
class FaceAnalysisResult:
    width: int
    height: int
    faces: list[DetectedFace]
    quality: Quality
    embedding: list[float] | None  # of the largest face, unit-normalized

    @property
    def primary(self) -> DetectedFace | None:
        return max(self.faces, key=lambda f: f.area) if self.faces else None


class FaceEngine(Protocol):
    name: str

    def ready(self) -> bool: ...

    def load(self) -> None: ...

    def detect(self, image_bgr: NDArray[np.uint8]) -> list[DetectedFace]: ...


class InsightFaceEngine:
    """insightface `FaceAnalysis` (antelopev2: SCRFD detector + glintr100 ArcFace + 3D landmarks for pose)."""

    name = "insightface"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._app: Any = None
        self._lock = threading.Lock()

    def ready(self) -> bool:
        return self._app is not None

    def model_dir(self) -> Path:
        return self._settings.resolved_face_model_root / "models" / self._settings.face_model_name

    def load(self) -> None:
        with self._lock:
            if self._app is not None:
                return
            model_dir = self.model_dir()
            if not model_dir.is_dir() or not list(model_dir.glob("*.onnx")):
                raise RuntimeError(
                    f"insightface のモデルがありません: {model_dir}"
                    "（scripts/install_models.sh で antelopev2 を配置してください）"
                )
            from insightface.app import FaceAnalysis  # noqa: PLC0415 - heavy import, only when used

            app = FaceAnalysis(
                name=self._settings.face_model_name,
                root=str(self._settings.resolved_face_model_root),
                providers=["CPUExecutionProvider"],
                allowed_modules=["detection", "recognition", "landmark_3d_68"],
            )
            size = self._settings.face_det_size
            app.prepare(ctx_id=-1, det_thresh=self._settings.face_det_thresh, det_size=(size, size))
            self._app = app
            logger.info("insightface ready: %s (%s)", self._settings.face_model_name, model_dir)

    def detect(self, image_bgr: NDArray[np.uint8]) -> list[DetectedFace]:
        if self._app is None:
            self.load()
        faces = self._app.get(image_bgr)
        result: list[DetectedFace] = []
        for face in faces:
            emb = getattr(face, "normed_embedding", None)
            if emb is None:
                continue
            pose = face.get("pose") if hasattr(face, "get") else None
            yaw = pitch = roll = None
            if pose is not None and len(pose) == 3:
                pitch, yaw, roll = (float(pose[0]), float(pose[1]), float(pose[2]))
            bbox = face.bbox
            result.append(
                DetectedFace(
                    bbox=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
                    det_score=float(face.det_score),
                    embedding=np.asarray(emb, dtype=np.float32),
                    yaw=yaw,
                    pitch=pitch,
                    roll=roll,
                )
            )
        return result


class MockFaceEngine:
    """Deterministic engine for tests. A "face" is a rectangle of pixels with R == 255 and B == 255 (magenta-ish);
    G encodes the identity (embedding seed), and the rectangle's aspect encodes nothing else.
    Pose is read from the top-left marker pixel's G channel of the *image* corner (0..255 -> yaw -60..60 deg)."""

    name = "mock"

    def ready(self) -> bool:
        return True

    def load(self) -> None:
        return None

    @staticmethod
    def embedding_for(identity: int) -> NDArray[np.float32]:
        rng = np.random.default_rng(identity)
        vec = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
        return vec / np.linalg.norm(vec)

    def detect(self, image_bgr: NDArray[np.uint8]) -> list[DetectedFace]:
        b, g, r = image_bgr[..., 0], image_bgr[..., 1], image_bgr[..., 2]
        mask = (r == 255) & (b == 255) & (g < 250)
        if not mask.any():
            return []
        num, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
        faces: list[DetectedFace] = []
        yaw_code = int(image_bgr[0, 0, 1])
        yaw = (yaw_code / 255.0) * 120.0 - 60.0 if image_bgr[0, 0, 2] == 255 and image_bgr[0, 0, 0] == 255 else 0.0
        for label in range(1, num):
            x, y, w, h, area = (int(v) for v in stats[label])
            if area < 64:
                continue
            identity = int(np.median(g[labels == label]))
            faces.append(
                DetectedFace(
                    bbox=(float(x), float(y), float(x + w), float(y + h)),
                    det_score=0.95,
                    embedding=self.embedding_for(identity),
                    yaw=yaw,
                    pitch=0.0,
                    roll=0.0,
                )
            )
        return faces


def create_engine(settings: Settings) -> FaceEngine:
    if settings.face_engine == "mock":
        return MockFaceEngine()
    return InsightFaceEngine(settings)


# ----------------------------------------------------------------------------- image helpers
def decode_image(data: bytes) -> NDArray[np.uint8]:
    """Bytes (png / jpg / webp) -> BGR uint8 array with EXIF orientation applied."""
    with Image.open(io.BytesIO(data)) as raw:
        im = ImageOps.exif_transpose(raw) or raw
        rgb = np.asarray(im.convert("RGB"), dtype=np.uint8)
    return np.ascontiguousarray(rgb[..., ::-1])


def load_image(path: Path) -> NDArray[np.uint8]:
    return decode_image(path.read_bytes())


def sharpness_of(image_bgr: NDArray[np.uint8], bbox: tuple[float, float, float, float]) -> float:
    h, w = image_bgr.shape[:2]
    x1, y1, x2, y2 = (
        int(max(0, min(w, bbox[0]))),
        int(max(0, min(h, bbox[1]))),
        int(max(0, min(w, bbox[2]))),
        int(max(0, min(h, bbox[3]))),
    )
    if x2 - x1 < 4 or y2 - y1 < 4:
        return 0.0
    crop = image_bgr[y1:y2, x1:x2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    scale = CROP_WIDTH / gray.shape[1]
    if abs(scale - 1.0) > 0.01:
        gray = cv2.resize(gray, (CROP_WIDTH, max(1, int(gray.shape[0] * scale))), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def cosine_similarity(a: list[float] | NDArray[np.float32], b: list[float] | NDArray[np.float32]) -> float:
    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def cluster_by_similarity(matrix: list[list[float | None]], threshold: float) -> list[list[int]]:
    """Union-find over pairs with similarity >= threshold. Items without an embedding stay singletons."""
    n = len(matrix)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            sim = matrix[i][j]
            if sim is not None and sim >= threshold:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=lambda g: (-len(g), g[0]))


# ----------------------------------------------------------------------------- service
@dataclass(slots=True)
class Thresholds:
    face_ratio_min: float
    det_score_min: float
    yaw_max_deg: float
    sharpness_min: float
    recommend_min: float
    acceptable_min: float
    cluster: float
    similarity_good: float
    similarity_acceptable: float

    @classmethod
    def from_settings(cls, s: Settings) -> Thresholds:
        return cls(
            face_ratio_min=s.quality_face_ratio_min,
            det_score_min=s.quality_det_score_min,
            yaw_max_deg=s.quality_yaw_max_deg,
            sharpness_min=s.quality_sharpness_min,
            recommend_min=s.quality_recommend_min,
            acceptable_min=s.quality_acceptable_min,
            cluster=s.cluster_threshold,
            similarity_good=s.similarity_good,
            similarity_acceptable=s.similarity_acceptable,
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "face_ratio_min": self.face_ratio_min,
            "det_score_min": self.det_score_min,
            "yaw_max_deg": self.yaw_max_deg,
            "sharpness_min": self.sharpness_min,
            "recommend_min": self.recommend_min,
            "acceptable_min": self.acceptable_min,
            "cluster": self.cluster,
            "similarity_good": self.similarity_good,
            "similarity_acceptable": self.similarity_acceptable,
        }


@dataclass(slots=True)
class Recommendation:
    index: int | None
    reason: str
    reasons: list[str] = field(default_factory=list)


class FaceService:
    def __init__(self, engine: FaceEngine, thresholds: Thresholds) -> None:
        self.engine = engine
        self.thresholds = thresholds

    # -- quality ---------------------------------------------------------------
    def quality_of(self, image_bgr: NDArray[np.uint8], faces: list[DetectedFace]) -> Quality:
        t = self.thresholds
        h, w = image_bgr.shape[:2]
        if not faces:
            return Quality(
                face_count=0,
                face_ratio=0.0,
                det_score=0.0,
                sharpness=0.0,
                yaw=0.0,
                pitch=0.0,
                roll=0.0,
                composite=0.0,
                warnings=["no_face"],
                usable=False,
                grade="unusable",
            )
        face = max(faces, key=lambda f: f.area)
        face_ratio = face.area / float(w * h) if w * h else 0.0
        sharp = sharpness_of(image_bgr, face.bbox)
        warnings: list[str] = []
        if len(faces) > 1:
            warnings.append("multiple_faces")
        if face_ratio < t.face_ratio_min:
            warnings.append("face_too_small")
        if face.det_score < t.det_score_min:
            warnings.append("low_det_score")
        yaw = face.yaw if face.yaw is not None else 0.0
        pitch = face.pitch if face.pitch is not None else 0.0
        roll = face.roll if face.roll is not None else 0.0
        if face.yaw is None:
            warnings.append("pose_unavailable")
        elif abs(yaw) > t.yaw_max_deg:
            warnings.append("not_frontal")
        if sharp < t.sharpness_min:
            warnings.append("blurry")

        frontal = max(0.0, 1.0 - abs(yaw) / FRONTAL_FULL_DEG) * max(0.0, 1.0 - abs(pitch) / FRONTAL_FULL_DEG)
        frontal *= max(0.0, 1.0 - abs(roll) / ROLL_FULL_DEG)
        sub = {
            "frontal": frontal,
            "sharpness": min(1.0, sharp / SHARPNESS_FULL_SCORE),
            "face_ratio": min(1.0, face_ratio / FACE_RATIO_FULL_SCORE),
            "det_score": min(1.0, max(0.0, face.det_score)),
        }
        composite = sum(COMPOSITE_WEIGHTS[k] * v for k, v in sub.items())
        composite = round(max(0.0, min(1.0, composite)), 4)
        grade: Literal["recommended", "acceptable", "not_recommended"]
        if composite >= t.recommend_min and not warnings:
            grade = "recommended"
        elif composite >= t.acceptable_min:
            grade = "acceptable"
        else:
            grade = "not_recommended"
        return Quality(
            face_count=len(faces),
            face_ratio=round(face_ratio, 4),
            det_score=round(face.det_score, 4),
            sharpness=round(sharp, 2),
            yaw=round(yaw, 2),
            pitch=round(pitch, 2),
            roll=round(roll, 2),
            composite=composite,
            warnings=warnings,
            usable=True,
            grade=grade,
        )

    def analyze(self, image_bgr: NDArray[np.uint8]) -> FaceAnalysisResult:
        faces = self.engine.detect(image_bgr)
        quality = self.quality_of(image_bgr, faces)
        primary = max(faces, key=lambda f: f.area) if faces else None
        embedding = [float(x) for x in primary.embedding] if primary is not None else None
        h, w = image_bgr.shape[:2]
        return FaceAnalysisResult(width=w, height=h, faces=faces, quality=quality, embedding=embedding)

    def analyze_bytes(self, data: bytes) -> FaceAnalysisResult:
        return self.analyze(decode_image(data))

    def analyze_path(self, path: Path) -> FaceAnalysisResult:
        return self.analyze(load_image(path))

    # -- similarity / clustering / recommendation ---------------------------------
    def similarity_matrix(self, embeddings: list[list[float] | None]) -> list[list[float | None]]:
        n = len(embeddings)
        matrix: list[list[float | None]] = [[None] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                a, b = embeddings[i], embeddings[j]
                if a is None or b is None:
                    continue
                matrix[i][j] = 1.0 if i == j else round(cosine_similarity(a, b), 4)
        return matrix

    def clusters(self, matrix: list[list[float | None]]) -> list[list[int]]:
        return cluster_by_similarity(matrix, self.thresholds.cluster)

    def recommend(self, qualities: list[Quality | None], clusters: list[list[int]]) -> Recommendation:
        candidates = [i for i, q in enumerate(qualities) if q is not None and q.usable and q.face_count == 1]
        if not candidates:
            candidates = [i for i, q in enumerate(qualities) if q is not None and q.usable]
        if not candidates:
            return Recommendation(
                index=None, reason="使用できる参照顔がありません（顔が検出できる画像を用意してください）"
            )
        largest = next((c for c in clusters if any(i in candidates for i in c)), None)
        pool = [i for i in (largest or candidates) if i in candidates] or candidates
        scored = [(i, q) for i in pool if (q := qualities[i]) is not None]
        best, q = max(scored, key=lambda pair: pair[1].composite)
        reasons: list[str] = []
        t = self.thresholds
        reasons.append("正面" if abs(q.yaw) <= t.yaw_max_deg else f"やや横向き（yaw {q.yaw:+.0f}°）")
        reasons.append(
            "高精細"
            if q.sharpness >= SHARPNESS_FULL_SCORE
            else ("シャープネス十分" if q.sharpness >= t.sharpness_min else "ややぼけ")
        )
        reasons.append("顔サイズ十分" if q.face_ratio >= t.face_ratio_min else "顔が小さめ")
        if largest and len(largest) > 1 and best in largest:
            reasons.append(f"同一人物グループ（{len(largest)}枚）の中で最高スコア")
        return Recommendation(index=best, reason="・".join(reasons), reasons=reasons)

    def similarity_grade(self, similarity: float | None) -> SimilarityGrade:
        if similarity is None:
            return "unknown"
        if similarity >= self.thresholds.similarity_good:
            return "good"
        if similarity >= self.thresholds.similarity_acceptable:
            return "acceptable"
        return "warning"


def embedding_id(embedding: list[float]) -> str:
    return "e_" + hashlib.sha1(np.asarray(embedding, dtype=np.float32).tobytes()).hexdigest()[:12]  # noqa: S324 - id only
