"""非同期ジョブのワーカーとスケジューラの常駐プロセス（`python -m app.worker`）。

本番（Fly.io）は API と同じイメージから `worker` プロセスグループとして動かす（apps/api/fly.toml）。
API プロセスは ENGINE_WORKER_ENABLED=false / ENGINE_SCHEDULER_ENABLED=false にし、ジョブの実行と定期実行は
このプロセスに任せる（返答のレイテンシ（E8）に影響させない）。このコマンドは環境変数に関係なく
ワーカーとスケジューラを起動する（`--no-scheduler` / `--no-worker` で片方だけにできる）。
スケジューラは pg_try_advisory_lock でリーダーを決めるので、複数台で動かしても定期実行は二重にならない。

SIGTERM / SIGINT で新しいジョブを取るのをやめ、実行中のジョブの完了を待ってから終了する。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys

from app import __version__
from app.container import build_services
from app.core.config import get_settings
from app.core.db import create_pool
from app.core.http import create_upstream_client
from app.core.logging import configure_logging, get_logger
from app.core.observability import init_sentry
from app.services.persona import PersonaRepository
from app.services.prompt import PromptBuilder

logger = get_logger("worker")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.worker", description="everkano engine worker / scheduler")
    parser.add_argument("--no-worker", action="store_true", help="ジョブのワーカーを動かさない")
    parser.add_argument("--no-scheduler", action="store_true", help="スケジューラを動かさない")
    return parser.parse_args(argv)


async def run(*, worker_enabled: bool = True, scheduler_enabled: bool = True) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    init_sentry(settings)
    personas = PersonaRepository.load_dir(settings.resolved_personas_dir)
    prompts = PromptBuilder.load_dir(settings.resolved_prompts_dir)
    http = create_upstream_client(settings.llm_timeout_seconds)
    try:
        pool = await create_pool(settings)
    except BaseException:
        await http.aclose()
        raise
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # pragma: no cover - Windows
            loop.add_signal_handler(sig, stop.set)
    try:
        services = build_services(settings=settings, pool=pool, http=http, personas=personas, prompts=prompts)
        engine = services.engine
        if worker_enabled:
            engine.worker.start()
        if scheduler_enabled:
            engine.scheduler.start()
        logger.info(
            "engine worker process started",
            extra={
                "fields": {
                    "version": __version__,
                    "env": settings.app_env,
                    "worker": worker_enabled,
                    "scheduler": scheduler_enabled,
                    "concurrency": settings.engine_worker_concurrency,
                    "job_kinds": engine.registry.kinds(),
                    "periodic_tasks": engine.scheduler.task_names,
                    "llm_mode": settings.llm_mode,
                }
            },
        )
        await stop.wait()
        logger.info("engine worker process stopping")
        await engine.scheduler.stop()
        await engine.worker.stop()
    finally:
        await pool.close()
        await http.aclose()
        logger.info("engine worker process stopped")


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    asyncio.run(run(worker_enabled=not args.no_worker, scheduler_enabled=not args.no_scheduler))


if __name__ == "__main__":
    main()
