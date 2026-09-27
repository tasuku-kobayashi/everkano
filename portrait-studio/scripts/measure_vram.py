"""Measure peak VRAM per (method, resolution, upscale, face_detailer) on THIS GPU -> api/app/data/vram_table.json.

    cd portrait-studio/api
    uv run python ../scripts/measure_vram.py --checkpoint <file in models/checkpoints> [--ref face.png]

How it measures: for every combination, ComfyUI is asked to free memory (POST /free), then one image is generated
with the real workflow (app.workflow.build_prompt, batch_size 1) while GET /system_stats is sampled every 200 ms.
peak_mb is the highest observed (vram_total - vram_free), i.e. what the driver reports for the whole GPU during the
run (ComfyUI's baseline and other processes included). A combination that raises an error (OOM) is recorded with
peak_mb = null and the error in `note`, so the API keeps rejecting it. No value in the table is ever estimated.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

API_DIR = Path(__file__).resolve().parents[1] / "api"
sys.path.insert(0, str(API_DIR))

from app.comfy_client import ComfyClient, ComfyError  # noqa: E402
from app.models import DEFAULT_NEGATIVE_PROMPT, DEFAULT_PREFIX_PROMPT  # noqa: E402
from app.workflow import GenerationParams, build_prompt, load_all  # noqa: E402

DEFAULT_COMBOS: list[tuple[str, int, int, float, bool]] = [
    ("txt2img", 832, 1216, 1.0, False),
    ("pulid", 832, 1216, 1.0, False),
    ("pulid", 832, 1216, 1.0, True),
    ("pulid", 832, 1216, 1.3, True),
    ("faceid", 832, 1216, 1.0, True),
    ("instantid", 832, 1216, 1.0, True),
    ("pulid", 1024, 1536, 1.0, True),
]

VERIFY_PROMPT = "close-up portrait, looking at the camera, neutral expression, even soft lighting, plain background"


async def sample_peak(client: ComfyClient, stop: asyncio.Event, samples: list[int], interval: float) -> None:
    while not stop.is_set():
        try:
            stats = await client.system_stats()
            samples.append(stats.vram_total_mb - stats.vram_free_mb)
        except ComfyError:
            pass
        await asyncio.sleep(interval)


async def make_reference(client: ComfyClient, workflows: Any, checkpoint: str, out: Path) -> Path:
    """Generate one txt2img face to use as the reference for the face workflows."""
    params = GenerationParams(
        checkpoint=checkpoint,
        positive=f"{DEFAULT_PREFIX_PROMPT}, {VERIFY_PROMPT}",
        negative=DEFAULT_NEGATIVE_PROMPT,
        width=832,
        height=1216,
        seed=1234,
        face_detailer=False,
        filename_prefix="portrait-studio/measure/ref",
    )
    result = await client.run_prompt(build_prompt(workflows["txt2img"], params), timeout=900)
    out.write_bytes(await client.view(result.images[0]))
    print(f"reference generated: {out}")
    return out


async def run(args: argparse.Namespace) -> int:
    client = ComfyClient(args.comfy, timeout=60)
    workflows = load_all(Path(args.workflows))
    stats = await client.system_stats()
    print(
        f"GPU: {stats.gpu_name} | VRAM total {stats.vram_total_mb} MB | "
        f"torch {stats.pytorch_version} | comfy {stats.comfy_version}"
    )
    ref_path = (
        Path(args.ref)
        if args.ref
        else await make_reference(
            client, workflows, args.checkpoint, Path(tempfile.gettempdir()) / "portrait-studio-measure-ref.png"
        )
    )
    ref_name = await client.upload_image(ref_path.read_bytes(), "measure_ref.png", subfolder="refs")

    out_path = Path(args.out)
    table: dict[str, Any] = {
        "gpu": stats.gpu_name,
        "vram_total_mb": stats.vram_total_mb,
        "measured_at": None,
        "entries": [],
    }
    if out_path.is_file() and not args.reset:
        table = json.loads(out_path.read_text(encoding="utf-8"))
        previous = (table.get("checkpoint") or None, table.get("lora") or None)
        if any(e.get("peak_mb") is not None for e in table.get("entries", [])) and previous != (
            args.checkpoint,
            args.lora,
        ):
            print(
                f"ERROR: {out_path} was measured with checkpoint={previous[0]!r} lora={previous[1]!r}; "
                f"re-measuring with checkpoint={args.checkpoint!r} lora={args.lora!r} would mix two models. "
                "Use --reset (new table) or --out <other file>.",
                file=sys.stderr,
            )
            await client.aclose()
            return 2
    entries: dict[tuple[str, int, int, float, bool], dict[str, Any]] = {
        (e["method"], e["width"], e["height"], float(e.get("upscale", 1.0)), bool(e.get("face_detailer", False))): e
        for e in table.get("entries", [])
    }

    combos = DEFAULT_COMBOS if not args.only else [c for c in DEFAULT_COMBOS if c[0] in set(args.only)]
    for method, width, height, upscale, fd in combos:
        if method not in workflows:
            print(f"skip {method}: no workflow")
            continue
        label = f"{method} {width}x{height} x{upscale:g} fd={'on' if fd else 'off'}"
        peaks: list[int] = []
        note: str | None = None
        for rep in range(args.repeat):
            try:
                await client.free(unload_models=True, free_memory=True)
                await asyncio.sleep(2.0)
                params = GenerationParams(
                    checkpoint=args.checkpoint,
                    positive=f"{DEFAULT_PREFIX_PROMPT}, {VERIFY_PROMPT}",
                    negative=DEFAULT_NEGATIVE_PROMPT,
                    width=width,
                    height=height,
                    seed=1000 + rep,
                    upscale=upscale,
                    face_method=None if method == "txt2img" else method,
                    face_weight=0.8,
                    ref_image=ref_name,
                    face_detailer=fd,
                    lora=args.lora,
                    lora_strength=args.lora_strength if args.lora else 0.0,
                    filename_prefix=f"portrait-studio/measure/{method}",
                )
                prompt = build_prompt(workflows[method], params)
                samples: list[int] = []
                stop = asyncio.Event()
                sampler = asyncio.create_task(sample_peak(client, stop, samples, args.interval))
                try:
                    result = await client.run_prompt(prompt, timeout=args.timeout)
                finally:
                    stop.set()
                    await sampler
                peak = max(samples) if samples else 0
                peaks.append(peak)
                print(f"{label}: run {rep + 1}: peak {peak} MB ({len(samples)} samples, {result.duration_ms} ms)")
            except ComfyError as exc:
                note = f"error: {exc}"
                print(f"{label}: ERROR {exc}")
                break
        entry = {
            "method": method,
            "width": width,
            "height": height,
            "upscale": upscale,
            "face_detailer": fd,
            "peak_mb": max(peaks) if peaks and note is None else None,
            "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "note": note or f"max of {len(peaks)} run(s); sampled every {args.interval}s via /system_stats",
        }
        entries[(method, width, height, upscale, fd)] = entry

    table["gpu"] = stats.gpu_name
    table["vram_total_mb"] = stats.vram_total_mb
    table["measured_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    # the API returns `unknown` (rejects) for a different checkpoint / LoRA than the one measured here
    table["checkpoint"] = args.checkpoint
    table["lora"] = args.lora
    table["note"] = (
        "Measured by scripts/measure_vram.py (peak of vram_total - vram_free sampled during one generation) "
        f"with checkpoint {args.checkpoint!r} and LoRA {args.lora!r}. Unknown rows are rejected by the API."
    )
    table["entries"] = list(entries.values())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(table, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out_path}")
    print(json.dumps(table, ensure_ascii=False, indent=2))
    await client.aclose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--comfy", default=os.environ.get("COMFY_URL", "http://127.0.0.1:8188"))
    parser.add_argument(
        "--checkpoint", default=os.environ.get("DEFAULT_CHECKPOINT"), help="file name under models/checkpoints"
    )
    parser.add_argument("--lora", default=None, help="measure with this LoRA (file name under models/loras)")
    parser.add_argument("--lora-strength", type=float, default=0.8)
    parser.add_argument("--ref", help="reference face PNG (generated with txt2img when omitted)")
    parser.add_argument("--workflows", default=str(API_DIR.parent / "workflows"))
    parser.add_argument("--out", default=str(API_DIR / "app" / "data" / "vram_table.json"))
    parser.add_argument("--only", nargs="*", help="restrict to these methods")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--reset", action="store_true", help="ignore the existing table instead of merging")
    args = parser.parse_args()
    if not args.checkpoint:
        parser.error("--checkpoint (or DEFAULT_CHECKPOINT in .env) is required")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
