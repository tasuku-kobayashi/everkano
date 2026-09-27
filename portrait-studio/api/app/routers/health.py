"""GET /api/health (no authentication)."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app import __version__
from app.comfy_client import ComfyError
from app.container import Services
from app.routers.common import ServicesDep
from app.schemas import HealthResponse, WorkflowStatus
from app.workflow import workflow_ok

router = APIRouter(tags=["health"])


def _workflow_statuses(s: Services) -> list[WorkflowStatus]:
    return [
        WorkflowStatus(
            method=method,
            file=wf.path.name,
            ok=(not s.workflow_checked) or workflow_ok(s.workflow_issues.get(method, [])),
            issues=s.workflow_issues.get(method, []),
        )
        for method, wf in s.workflows.items()
    ]


@router.get("/health", response_model=HealthResponse, responses={503: {"model": HealthResponse}})
async def health(s: ServicesDep, response: Response) -> HealthResponse:
    try:
        stats = await s.comfy.system_stats()
        checkpoints = await s.comfy.checkpoints()
        if not s.workflow_checked:
            await s.check_workflows()
    except ComfyError as exc:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            ok=False,
            comfy_connected=False,
            face_engine=s.face_engine.name,
            face_engine_ready=s.face_engine.ready(),
            workflows=_workflow_statuses(s),
            version=__version__,
            error=str(exc),
        )
    workflows = _workflow_statuses(s)
    return HealthResponse(
        ok=True,
        gpu=stats.gpu_name,
        vram_total_mb=stats.vram_total_mb,
        vram_free_mb=stats.vram_free_mb,
        torch=stats.pytorch_version,
        cuda=stats.cuda_version,
        comfy_version=stats.comfy_version,
        checkpoints=checkpoints,
        face_methods=s.available_face_methods(),
        comfy_connected=True,
        face_engine=s.face_engine.name,
        face_engine_ready=s.face_engine.ready(),
        workflows=workflows,
        version=__version__,
    )
