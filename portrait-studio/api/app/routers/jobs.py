"""GET /api/jobs/{id}, GET /api/jobs?status=, POST /api/jobs/{id}/cancel."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.routers.common import ServicesDep, not_found
from app.schemas import JobListResponse, JobSchema
from app.security import require_api_key
from app.serialize import job_schema

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=JobListResponse)
async def list_jobs(
    s: ServicesDep,
    status: Annotated[str | None, Query(pattern="^(queued|running|done|error|canceled)$")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> JobListResponse:
    jobs = s.db.list_jobs(status=status, limit=limit)
    return JobListResponse(items=[job_schema(j, s.queue.position(j)) for j in jobs])


@router.get("/{job_id}", response_model=JobSchema)
async def get_job(job_id: str, s: ServicesDep) -> JobSchema:
    job = s.db.get_job(job_id)
    if job is None:
        raise not_found("ジョブが見つかりません")
    return job_schema(job, s.queue.position(job))


@router.post("/{job_id}/cancel", response_model=JobSchema)
async def cancel_job(job_id: str, s: ServicesDep) -> JobSchema:
    job = await s.queue.cancel(job_id)
    if job is None:
        raise not_found("ジョブが見つかりません")
    return job_schema(job, s.queue.position(job))
