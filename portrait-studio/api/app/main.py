"""FastAPI application. `uvicorn app.main:app --host 127.0.0.1 --port 8000`.

- All API routes live under /api. `GET /api/health` is the only unauthenticated route. Image / thumbnail / reference
  file bytes (GET) additionally accept the `psk` cookie (see app.security); everything else is header-only.
- The built web UI (web/dist) is served from `/` with an SPA fallback when the directory exists.
- Startup fails loudly when API_KEY is missing or a workflow file lacks a required `_meta.title`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.comfy_client import ComfyError, ComfyUnavailable
from app.config import Settings, get_settings
from app.container import Services, build_services
from app.face import FaceEngine
from app.routers import audit, characters, generate, health, images, jobs, presets, system

logger = logging.getLogger("portrait")

API_TITLE: Final[str] = "portrait-studio API"
API_DESCRIPTION: Final[str] = (
    "同一の架空キャラクター（実写調・日本人・成人）で画像を生成・管理するローカル専用 API。"
    '認証: ヘッダ `X-API-Key`（`GET /api/health` を除く）。エラーは `{"detail": "..."}`（日本語）。'
)


def create_app(settings: Settings | None = None, *, face_engine: FaceEngine | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        services = build_services(settings, face_engine=face_engine)
        app.state.services = services
        await services.start()
        logger.info(
            "portrait-studio API %s ready: comfy=%s data=%s workflows=%s face_engine=%s",
            __version__,
            settings.comfy_url,
            settings.data_dir,
            sorted(services.workflows),
            services.face_engine.name,
        )
        try:
            yield
        finally:
            await services.stop()

    app = FastAPI(title=API_TITLE, description=API_DESCRIPTION, version=__version__, lifespan=lifespan)
    app.state.settings = settings

    @app.exception_handler(ComfyUnavailable)
    async def _comfy_unavailable(_request: Request, exc: ComfyUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(ComfyError)
    async def _comfy_error(_request: Request, exc: ComfyError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    for router in (
        health.router,
        characters.router,
        characters.files_router,  # header OR psk cookie: reference face bytes for <img src>
        generate.router,
        jobs.router,
        images.router,
        images.files_router,  # header OR psk cookie: image / thumbnail bytes for <img src>
        presets.router,
        system.router,
        audit.router,
    ):
        app.include_router(router, prefix="/api")

    mount_static(app, settings.web_dist_dir)
    return app


def mount_static(app: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    if not index.is_file():
        logger.warning("web UI not built (%s missing); only /api is served", index)

        @app.get("/", include_in_schema=False)
        async def _root() -> JSONResponse:
            return JSONResponse(
                {
                    "service": API_TITLE,
                    "docs": "/docs",
                    "hint": "web/ を build すると同じポートで UI を配信します（README）",
                }
            )

        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(str(candidate))
        return FileResponse(str(index))


def get_app() -> FastAPI:
    return create_app()


def __getattr__(name: str) -> FastAPI:
    """`uvicorn app.main:app` builds the app on first access (PEP 562), so importing this module never needs API_KEY."""
    if name == "app":
        return create_app()
    raise AttributeError(name)


__all__ = ["Services", "create_app", "get_app"]
