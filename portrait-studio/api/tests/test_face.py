from __future__ import annotations

import numpy as np

from app.face import (
    COMPOSITE_WEIGHTS,
    FaceService,
    MockFaceEngine,
    Thresholds,
    cluster_by_similarity,
    cosine_similarity,
    decode_image,
)
from tests.helpers import face_png, landscape_png, two_faces_png

T = Thresholds(
    face_ratio_min=0.08,
    det_score_min=0.6,
    yaw_max_deg=15.0,
    sharpness_min=60.0,
    recommend_min=0.75,
    acceptable_min=0.5,
    cluster=0.5,
    similarity_good=0.75,
    similarity_acceptable=0.6,
)


def service() -> FaceService:
    return FaceService(MockFaceEngine(), T)


def test_composite_weights_sum_to_one() -> None:
    assert abs(sum(COMPOSITE_WEIGHTS.values()) - 1.0) < 1e-9


def test_no_face_is_unusable() -> None:
    q = service().analyze_bytes(landscape_png()).quality
    assert q.face_count == 0 and q.usable is False and q.grade == "unusable"
    assert "no_face" in q.warnings and q.composite == 0.0


def test_frontal_sharp_face_is_recommended() -> None:
    r = service().analyze_bytes(face_png(42))
    q = r.quality
    assert q.face_count == 1 and q.usable
    assert q.face_ratio > 0.08 and q.det_score > 0.6 and q.sharpness > 60 and abs(q.yaw) < 1
    assert q.grade == "recommended", q
    assert r.embedding is not None and len(r.embedding) == 512
    assert abs(float(np.linalg.norm(np.asarray(r.embedding))) - 1.0) < 1e-4


def test_profile_and_blur_and_small_face_warn() -> None:
    s = service()
    q = s.analyze_bytes(face_png(42, yaw_deg=40)).quality
    assert "not_frontal" in q.warnings and q.grade != "recommended"
    q = s.analyze_bytes(face_png(42, blur=True)).quality
    assert "blurry" in q.warnings
    q = s.analyze_bytes(face_png(42, width=1600, height=1600, face_scale=0.12)).quality
    assert "face_too_small" in q.warnings
    q = s.analyze_bytes(two_faces_png(10, 20)).quality
    assert q.face_count == 2 and "multiple_faces" in q.warnings


def test_similarity_matrix_clusters_and_recommendation() -> None:
    s = service()
    results = [
        s.analyze_bytes(face_png(5, seed=1)),
        s.analyze_bytes(face_png(5, seed=2)),
        s.analyze_bytes(face_png(200)),
        s.analyze_bytes(landscape_png()),
    ]
    matrix = s.similarity_matrix([r.embedding for r in results])
    assert matrix[0][1] is not None and matrix[0][1] > 0.99  # same identity
    assert matrix[0][2] is not None and matrix[0][2] < 0.3  # different identity
    assert matrix[0][3] is None and matrix[3][3] is None
    clusters = s.clusters(matrix)
    assert clusters[0] == [0, 1]
    rec = s.recommend([r.quality if r.quality.face_count else None for r in results], clusters)
    assert rec.index in (0, 1)
    assert "正面" in rec.reason and "同一人物グループ" in rec.reason


def test_cosine_and_union_find() -> None:
    assert abs(cosine_similarity([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9
    assert abs(cosine_similarity([1.0, 0.0], [0.0, 1.0])) < 1e-9
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
    m: list[list[float | None]] = [[1.0, 0.9, 0.1], [0.9, 1.0, 0.2], [0.1, 0.2, 1.0]]
    assert cluster_by_similarity(m, 0.5) == [[0, 1], [2]]


def test_decode_image_bgr_shape() -> None:
    arr = decode_image(face_png(1, width=300, height=200))
    assert arr.shape == (200, 300, 3) and arr.dtype == np.uint8
