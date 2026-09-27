"""ArcFace cosine similarity between images, and threshold calibration from same/different-character pairs.

    cd portrait-studio/api
    uv run python ../scripts/face_similarity.py pair a.png b.png
    uv run python ../scripts/face_similarity.py calibrate charA/ charB/ charC/ [--json out.json]

`calibrate` takes one directory per character (reference + generated images of the SAME character in each).
It reports the similarity distribution of same-character pairs (within a directory) and different-character pairs
(across directories), the equal-error-rate threshold, and the suggested SIMILARITY_ACCEPTABLE / SIMILARITY_GOOD
values. Record the output in docs/MODELS.md — the defaults 0.60 / 0.75 are only a starting point.
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1] / "api"
sys.path.insert(0, str(API_DIR))

from app.config import Settings  # noqa: E402
from app.face import FaceService, Thresholds, cosine_similarity, create_engine  # noqa: E402

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def make_service() -> FaceService:
    settings = Settings(api_key="face-similarity-script-dummy", _env_file=str(API_DIR.parent / ".env"))  # type: ignore[call-arg]
    return FaceService(create_engine(settings), Thresholds.from_settings(settings))


def embed_dir(service: FaceService, directory: Path) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for path in sorted(p for p in directory.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES):
        result = service.analyze_path(path)
        if result.embedding is None:
            print(f"  skip (no face): {path}")
            continue
        if result.quality.face_count > 1:
            print(f"  warn (multiple faces, largest used): {path}")
        out[str(path)] = result.embedding
    return out


def describe(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"n": 0}
    s = sorted(values)

    def pct(p: float) -> float:
        return s[min(len(s) - 1, round(p * (len(s) - 1)))]

    return {
        "n": len(s),
        "mean": round(statistics.fmean(s), 4),
        "std": round(statistics.pstdev(s), 4) if len(s) > 1 else 0.0,
        "min": round(s[0], 4),
        "p5": round(pct(0.05), 4),
        "p25": round(pct(0.25), 4),
        "median": round(pct(0.5), 4),
        "p95": round(pct(0.95), 4),
        "max": round(s[-1], 4),
    }


def eer_threshold(same: list[float], diff: list[float]) -> tuple[float, float, float]:
    """Threshold where false-accept (diff >= t) and false-reject (same < t) rates are closest."""
    best = (0.5, 1.0, 1.0)
    best_gap = 10.0
    for i in range(0, 101):
        t = i / 100
        far = sum(1 for v in diff if v >= t) / len(diff) if diff else 0.0
        frr = sum(1 for v in same if v < t) / len(same) if same else 0.0
        if abs(far - frr) < best_gap:
            best_gap = abs(far - frr)
            best = (t, far, frr)
    return best


def cmd_pair(args: argparse.Namespace) -> int:
    service = make_service()
    a = service.analyze_path(Path(args.a))
    b = service.analyze_path(Path(args.b))
    if a.embedding is None or b.embedding is None:
        print(
            json.dumps(
                {
                    "similarity": None,
                    "reason": "face not detected",
                    "a_faces": a.quality.face_count,
                    "b_faces": b.quality.face_count,
                }
            )
        )
        return 1
    sim = cosine_similarity(a.embedding, b.embedding)
    print(
        json.dumps(
            {
                "similarity": round(sim, 4),
                "grade": service.similarity_grade(sim),
                "a_quality": a.quality.to_dict(),
                "b_quality": b.quality.to_dict(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    service = make_service()
    groups: dict[str, dict[str, list[float]]] = {}
    for d in args.dirs:
        directory = Path(d)
        if not directory.is_dir():
            print(f"not a directory: {d}", file=sys.stderr)
            return 2
        print(f"== {directory}")
        groups[str(directory)] = embed_dir(service, directory)
        print(f"  {len(groups[str(directory)])} face(s)")
    same: list[float] = []
    diff: list[float] = []
    for name, embs in groups.items():
        vectors = list(embs.values())
        same.extend(cosine_similarity(x, y) for x, y in itertools.combinations(vectors, 2))
        for other, embs2 in groups.items():
            if other <= name:
                continue
            diff.extend(cosine_similarity(x, y) for x in vectors for y in embs2.values())
    t, far, frr = eer_threshold(same, diff)
    same_stats = describe(same)
    diff_stats = describe(diff)
    suggested_acceptable = round(max(t, float(diff_stats.get("p95", 0.0)) + 0.05), 2) if diff else None
    suggested_good = round(float(same_stats.get("p25", 0.75)), 2) if same else None
    report = {
        "groups": {k: len(v) for k, v in groups.items()},
        "same_character_pairs": same_stats,
        "different_character_pairs": diff_stats,
        "eer": {"threshold": t, "far": round(far, 4), "frr": round(frr, 4)},
        "suggested": {"SIMILARITY_ACCEPTABLE": suggested_acceptable, "SIMILARITY_GOOD": suggested_good},
        "reference_benchmarks": {
            "faceid_plus_v2": 0.62,
            "pulid": 0.69,
            "instantid": 0.78,
            "note": "published averages; a large deviation suggests an implementation problem",
        },
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json:
        Path(args.json).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.json}")
    if same and diff and float(same_stats["p5"]) <= float(diff_stats["p95"]):
        print(
            "WARNING: same- and different-character distributions overlap; "
            "check reference quality (frontal, sharp) before trusting the thresholds."
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pair", help="similarity of two images")
    p.add_argument("a")
    p.add_argument("b")
    p.set_defaults(func=cmd_pair)
    c = sub.add_parser("calibrate", help="threshold calibration from per-character directories")
    c.add_argument("dirs", nargs="+")
    c.add_argument("--json", help="write the report here")
    c.set_defaults(func=cmd_calibrate)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
