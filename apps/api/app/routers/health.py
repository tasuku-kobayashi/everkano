from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.container import ServicesDep
from app.core.db import check_database
from app.models.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", summary="死活監視（認証不要）")
async def health(services: ServicesDep) -> HealthResponse:
    settings = services.settings
    db_ok = await check_database(services.pool)
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        version=__version__,
        env=settings.app_env,
        llm_mode=settings.llm_mode,
        embedding_mode=settings.embedding_mode,
        db="ok" if db_ok else "error",
    )
