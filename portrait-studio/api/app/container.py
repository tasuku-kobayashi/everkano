"""Service wiring. `Services` lives on `app.state.services` and is built once in the lifespan."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.audit import AuditLog
from app.comfy_client import ComfyClient, ComfyError
from app.config import Settings
from app.db import Database
from app.face import FaceEngine, FaceService, Thresholds, create_engine
from app.jobs import JobRunner
from app.presets import PresetService
from app.queue import JobQueue
from app.storage import Storage
from app.vram import VramTable
from app.workflow import Workflow, load_all, validate_against_object_info, workflow_ok

logger = logging.getLogger("portrait.container")

VRAM_TABLE_PATH = Path(__file__).resolve().parent / "data" / "vram_table.json"


@dataclass
class Services:
    settings: Settings
    db: Database
    storage: Storage
    comfy: ComfyClient
    workflows: dict[str, Workflow]
    face_engine: FaceEngine
    face: FaceService
    vram: VramTable
    audit: AuditLog
    presets: PresetService
    runner: JobRunner = field(init=False)
    queue: JobQueue = field(init=False)
    workflow_issues: dict[str, list[str]] = field(default_factory=dict)
    workflow_checked: bool = False

    def __post_init__(self) -> None:
        self.runner = JobRunner(self)
        self.queue = JobQueue(self.db, self.runner)

    # -- workflows ---------------------------------------------------------------
    def available_face_methods(self) -> list[str]:
        methods = [m for m in self.workflows if m != "txt2img"]
        if not self.workflow_checked:
            return methods
        return [m for m in methods if workflow_ok(self.workflow_issues.get(m, []))]

    def workflow_usable(self, method: str) -> bool:
        if method not in self.workflows:
            return False
        return not self.workflow_checked or workflow_ok(self.workflow_issues.get(method, []))

    async def check_workflows(self) -> None:
        """Validate every workflow against the live /object_info (no-op when ComfyUI is unreachable)."""
        try:
            info = await self.comfy.object_info()
        except ComfyError as exc:
            logger.warning("workflow check skipped: %s", exc)
            return
        for method, wf in self.workflows.items():
            issues = validate_against_object_info(wf, info)
            self.workflow_issues[method] = issues
            for issue in issues:
                (logger.error if issue.startswith("error:") else logger.warning)("workflow %s: %s", wf.path.name, issue)
        self.workflow_checked = True

    # -- face engine -------------------------------------------------------------
    async def ensure_face_engine(self) -> None:
        if not self.face_engine.ready():
            await asyncio.to_thread(self.face_engine.load)

    # -- lifecycle -----------------------------------------------------------------
    async def start(self) -> None:
        interrupted = self.db.mark_interrupted_jobs()
        if interrupted:
            logger.warning("%d job(s) from a previous run were marked as error", interrupted)
        await self.queue.start()
        await self.check_workflows()

    async def stop(self) -> None:
        await self.queue.stop()
        await self.comfy.aclose()


def build_services(settings: Settings, *, face_engine: FaceEngine | None = None) -> Services:
    storage = Storage(settings.data_dir, settings.thumbnail_size)
    db = Database(storage.db_path())
    workflows = load_all(settings.workflows_dir)  # raises WorkflowError -> the process does not start
    engine = face_engine or create_engine(settings)
    return Services(
        settings=settings,
        db=db,
        storage=storage,
        comfy=ComfyClient(
            settings.comfy_url,
            timeout=settings.comfy_timeout_seconds,
            poll_interval=settings.comfy_poll_interval_seconds,
        ),
        workflows=workflows,
        face_engine=engine,
        face=FaceService(engine, Thresholds.from_settings(settings)),
        vram=VramTable.load(settings.vram_table_path or VRAM_TABLE_PATH),
        audit=AuditLog(settings.resolved_audit_log_path),
        presets=PresetService(db),
    )
