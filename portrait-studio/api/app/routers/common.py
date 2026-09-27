"""Shared dependencies and helpers for routers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.comfy_client import ComfyUnavailable
from app.container import Services
from app.vram import Assessment, assess


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


def bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


async def resolve_checkpoint(s: Services, requested: str | None) -> str:
    """Requested -> DEFAULT_CHECKPOINT -> first installed checkpoint (ComfyUI)."""
    if requested:
        return requested
    if s.settings.default_checkpoint:
        return s.settings.default_checkpoint
    try:
        names = await s.comfy.checkpoints()
    except ComfyUnavailable as exc:
        raise HTTPException(
            status_code=503, detail=f"{exc}（checkpoint を明示すれば接続無しでも登録できます）"
        ) from exc
    if not names:
        raise bad_request(
            "チェックポイントが配置されていません（models/checkpoints にモデルを置くか、checkpoint を指定してください）"
        )
    return names[0]


async def vram_assessment(
    s: Services, *, method: str, width: int, height: int, upscale: float, face_detailer: bool
) -> Assessment:
    stats = await s.comfy.system_stats()  # ComfyUnavailable -> 503 via the app-level handler
    estimate = s.vram.estimate(method, width, height, upscale, face_detailer)
    total = stats.vram_total_mb or s.vram.vram_total_mb
    return assess(
        estimate,
        free_mb=stats.vram_free_mb or None,
        total_mb=total,
        margin_mb=s.settings.vram_safety_margin_mb,
        allow_unmeasured=s.settings.vram_allow_unmeasured,
        method=method,
        width=width,
        height=height,
        upscale=upscale,
        face_detailer=face_detailer,
    )


def reject_if_dangerous(a: Assessment) -> None:
    if not a.would_reject:
        return
    if a.risk == "unknown":
        raise bad_request(f"VRAM 見積りが未実測のため拒否しました。{a.advice}")
    raise bad_request(
        f"OOMの可能性が高いため拒否しました（推定ピーク {a.estimated_peak_mb} MB / VRAM 総量 {a.total_mb} MB）。"
        f"{a.advice}"
    )


def validate_dimensions(s: Services, width: int, height: int) -> None:
    lo, hi = s.settings.min_dimension, s.settings.max_dimension
    if width % 8 or height % 8:
        raise bad_request("width / height は 8 の倍数にしてください")
    if not (lo <= width <= hi and lo <= height <= hi):
        raise bad_request(f"width / height は {lo}〜{hi} の範囲にしてください")
