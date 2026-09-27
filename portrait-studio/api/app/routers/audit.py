"""GET /api/audit?limit= — tail of logs/audit.jsonl (newest first)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.routers.common import ServicesDep
from app.schemas import AuditResponse
from app.security import require_api_key

router = APIRouter(tags=["audit"], dependencies=[Depends(require_api_key)])


@router.get("/audit", response_model=AuditResponse)
async def audit(s: ServicesDep, limit: Annotated[int, Query(ge=1, le=1000)] = 100) -> AuditResponse:
    return AuditResponse(items=s.audit.tail(limit), path=str(s.audit.path), total_lines=s.audit.count_lines())
