"""時計を進めながら定期実行・ジョブ・チャットを動かす（評価ハーネス用のドライバ）。

app.engine.pipeline_driver.EngineDriver と同じ考え方（時計を step ずつ進め、各時刻で scheduler.run_due(now) →
worker.run_until_idle(now)）だが、次の点が違う:
- `jobs.cleanup` は実行しない。早送りした時計（2030 年）で実行すると、共有 DB の他のセッション・テストの
  完了済みジョブを消し、実行中のジョブを「止まった」とみなして再実行に戻してしまうため
  （jobs.reclaim_stale / jobs.cleanup は対象を絞れない）。評価の後片付けは world.cleanup_run が行う。
- チャットは `/chat/stream` と同じ経路（ChatService.open_stream）で動かし、最初の文字（delta / replace）までの
  時間（E8）をハーネス側でも計測する。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

from app.container import Services
from app.core.security import CurrentUser
from app.engine.pipeline import DeltaEvent, DoneEvent, ErrorEvent, ReplaceEvent
from app.engine.types import ManualClock
from app.models.dm import ChatRequest, ChatResponse

EXCLUDED_TASKS: Final[tuple[str, ...]] = ("jobs.cleanup",)


@dataclass(frozen=True, slots=True)
class ChatOutcome:
    response: ChatResponse
    ttft_ms: float | None  # open_stream の呼び出しから最初の delta / replace まで
    total_ms: float
    replaced: str | None  # replace の理由（moderated / safety）


@dataclass(slots=True)
class DriverStats:
    chats: int = 0
    jobs: int = 0
    ticks: int = 0
    task_runs: dict[str, int] = field(default_factory=dict)
    task_errors: dict[str, int] = field(default_factory=dict)


class ChatFailedError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str | None) -> None:
        super().__init__(f"chat failed: {status_code} {code} {message or ''}".strip())
        self.status_code = status_code
        self.code = code


class EvalDriver:
    def __init__(
        self,
        services: Services,
        clock: ManualClock,
        *,
        character_ids: Sequence[UUID],
        user_ids: Sequence[UUID],
        conversation_ids: Sequence[UUID],
        run_scheduler: bool = True,
    ) -> None:
        if services.clock is not clock:
            raise ValueError("services must be built with the same ManualClock")
        self._services = services
        self._clock = clock
        self._run_scheduler = run_scheduler
        self._scope = sorted(str(c) for c in conversation_ids)
        services.engine.scope.character_ids = tuple(character_ids)
        services.engine.scope.user_ids = tuple(user_ids)
        scheduler = services.engine.scheduler
        self._tasks = [n for n in scheduler.task_names if n.rsplit(":", 1)[-1] not in EXCLUDED_TASKS]
        self.stats = DriverStats()

    @property
    def now(self) -> datetime:
        return self._clock.now()

    @property
    def task_names(self) -> list[str]:
        return list(self._tasks)

    async def tick(self) -> None:
        now = self._clock.now()
        self.stats.ticks += 1
        if self._run_scheduler and self._tasks:
            result = await self._services.engine.scheduler.run_due(now=now, only=self._tasks)
            for run in result.runs:
                name = run.name.rsplit(":", 1)[-1]
                self.stats.task_runs[name] = self.stats.task_runs.get(name, 0) + 1
                if not run.ok:
                    self.stats.task_errors[name] = self.stats.task_errors.get(name, 0) + 1
        self.stats.jobs += await self._services.engine.worker.run_until_idle(now=now, dedupe_keys=self._scope)

    async def advance_to(self, target: datetime, *, step: timedelta) -> None:
        """target まで step ずつ時計を進め、各時刻で tick() する（target ちょうどでも 1 回 tick する）。"""
        if step <= timedelta(0):
            raise ValueError("step must be positive")
        if target <= self._clock.now():
            await self.tick()
            return
        while self._clock.now() < target:
            self._clock.set(min(self._clock.now() + step, target))
            await self.tick()

    async def settle(self, conversation_ids: Sequence[UUID] | None = None) -> int:
        """登録済みのジョブを run_at に関係なく処理する（その発言の好感度の変化を切り出して測るとき）。"""
        keys = sorted(str(c) for c in conversation_ids) if conversation_ids is not None else self._scope
        processed = await self._services.engine.worker.run_until_idle(
            now=self._clock.now(), ignore_run_at=True, dedupe_keys=keys
        )
        self.stats.jobs += processed
        await self._services.chat.pipeline.drain()
        return processed

    async def chat(self, user_id: UUID, character_id: UUID, conversation_id: UUID, message: str) -> ChatOutcome:
        request = ChatRequest(character_id=character_id, conversation_id=conversation_id, message=message)
        started = time.perf_counter()
        channel = await self._services.chat.open_stream(CurrentUser(id=user_id, email=None), request)
        ttft: float | None = None
        replaced: str | None = None
        response: ChatResponse | None = None
        async for event in channel:
            if isinstance(event, DeltaEvent | ReplaceEvent) and ttft is None:
                ttft = (time.perf_counter() - started) * 1000
            if isinstance(event, ReplaceEvent):
                replaced = event.reason
            elif isinstance(event, DoneEvent):
                response = event.response
            elif isinstance(event, ErrorEvent):
                raise ChatFailedError(event.status_code, event.code, event.message)
        if channel.error is not None:
            raise channel.error
        if response is None:
            raise RuntimeError("chat stream ended without done")
        self.stats.chats += 1
        return ChatOutcome(
            response=response, ttft_ms=ttft, total_ms=(time.perf_counter() - started) * 1000, replaced=replaced
        )
