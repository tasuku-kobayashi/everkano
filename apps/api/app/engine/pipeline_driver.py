"""評価ハーネス・テスト用: 時計（ManualClock）を進めながら、チャット・ジョブ・定期実行を決定的に動かす。

使い方（仕様 §9.1 の時間の早送り）:

    clock = ManualClock(datetime(2026, 10, 1, 0, 0, tzinfo=UTC))
    app = create_app(make_settings(engine_worker_enabled=False, engine_scheduler_enabled=False), clock=clock)
    async with app.router.lifespan_context(app):
        driver = EngineDriver(app.state.services, clock)
        response = await driver.chat(user_id, character_id, conversation_id, "来週の木曜に面接なんだ")
        await driver.advance(timedelta(days=1), step=timedelta(minutes=10))  # 10 分ずつ: 定期実行 → ジョブ
        await driver.settle()  # 登録済みのジョブを時刻に関係なくすべて処理する

- `chat()` は HTTP を通さずに `/chat` と同じパイプラインを動かす（認証・レート制限は省く。所有者の確認はする）。
- `advance()` は時計を step ずつ進め、各時刻で `scheduler.run_due(now)` → `worker.run_until_idle(now)` を行う。
  定期実行は「今の時刻」で必要な処理をするので、自発メッセージ（予定の終了の直後など）を取りこぼさないよう、
  step はスケジューラの最短の間隔（calendar.tick = 5 分）〜 10 分程度にする。
- 返答後のジョブ（post_turn）は返答の ENGINE_POST_TURN_DELAY_SECONDS 後に実行可能になる。ターンの間で時計を
  進めるか、`settle()` を呼ぶ。
- `jobs.cleanup`（古いジョブの削除・止まったジョブの回収）は既定で実行しない。早送りした時計（2030 年など）で
  実行すると、共有 DB の他のセッション・テストの完了済みジョブを消し、実行中のジョブを「止まった」とみなして
  queued に戻してしまう（reclaim_stale / cleanup は対象を絞れない）。専用の DB で確かめたいときだけ
  `run_job_cleanup=True` にする（評価ハーネスの evals/driver.py も同じ理由で実行しない）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

from app.container import Services
from app.core.security import CurrentUser
from app.engine.types import ManualClock
from app.models.dm import ChatRequest, ChatResponse

# 時計を早送りする実行では動かさない定期実行（共有 DB の他のデータを変えてしまうもの）
UNSCOPED_TASKS: Final[tuple[str, ...]] = ("jobs.cleanup",)


@dataclass(slots=True)
class DriverStats:
    chats: int = 0
    jobs: int = 0
    task_runs: dict[str, int] = field(default_factory=dict)


class EngineDriver:
    def __init__(
        self,
        services: Services,
        clock: ManualClock,
        *,
        run_scheduler: bool = True,
        job_scope: set[str] | None = None,
        character_ids: Sequence[UUID] | None = None,
        user_ids: Sequence[UUID] | None = None,
        run_job_cleanup: bool = False,
    ) -> None:
        """job_scope: 処理するジョブの dedupe_key（会話 ID）の集合。None なら全件（評価ハーネス専用の DB 向け）。

        同じ DB を他のテストが同時に使う場合は、自分の会話だけを処理するよう集合を渡す（chat() した会話は自動で
        加わる）。
        character_ids / user_ids: 定期実行（予定の生成・状態・自発メッセージ・好感度の日次処理）の対象を絞る
        （services.engine.scope に設定する。None = 全員）。
        run_job_cleanup: `jobs.cleanup` も実行する（対象を絞れないため、専用の DB のときだけ）。
        """
        if services.clock is not clock:
            raise ValueError("services must be built with the same ManualClock (create_app(..., clock=clock))")
        self._services = services
        self._clock = clock
        self._run_scheduler = run_scheduler
        self._job_scope = job_scope
        services.engine.scope.character_ids = tuple(character_ids) if character_ids is not None else None
        services.engine.scope.user_ids = tuple(user_ids) if user_ids is not None else None
        scheduler = services.engine.scheduler
        excluded = () if run_job_cleanup else UNSCOPED_TASKS
        self._tasks = [n for n in scheduler.task_names if n.rsplit(":", 1)[-1] not in excluded]
        self.stats = DriverStats()

    def _scope(self) -> list[str] | None:
        return sorted(self._job_scope) if self._job_scope is not None else None

    @property
    def now(self) -> datetime:
        return self._clock.now()

    @property
    def task_names(self) -> list[str]:
        """tick() で動かす定期実行の名前（名前空間付き）。"""
        return list(self._tasks)

    async def chat(self, user_id: UUID, character_id: UUID, conversation_id: UUID, message: str) -> ChatResponse:
        """今の時計の時刻でユーザーが発言したことにして、返答を生成・保存する。"""
        request = ChatRequest(character_id=character_id, conversation_id=conversation_id, message=message)
        response = await self._services.chat.chat(CurrentUser(id=user_id, email=None), request)
        if self._job_scope is not None:
            self._job_scope.add(str(conversation_id))
        self.stats.chats += 1
        return response

    async def tick(self) -> None:
        """今の時刻で、期限の来た定期実行と、実行可能なジョブを処理する。"""
        now = self._clock.now()
        if self._run_scheduler and self._tasks:
            result = await self._services.engine.scheduler.run_due(now=now, only=self._tasks)
            for name in result.ran:
                self.stats.task_runs[name] = self.stats.task_runs.get(name, 0) + 1
        self.stats.jobs += await self._services.engine.worker.run_until_idle(now=now, dedupe_keys=self._scope())

    async def advance(self, delta: timedelta, *, step: timedelta = timedelta(minutes=10)) -> None:
        """時計を step ずつ delta だけ進め、各時刻で tick() する。"""
        if step <= timedelta(0):
            raise ValueError("step must be positive")
        target = self._clock.now() + delta
        while self._clock.now() < target:
            self._clock.set(min(self._clock.now() + step, target))
            await self.tick()

    async def settle(self) -> int:
        """登録済みのジョブを run_at に関係なくすべて処理する（ターンの直後に記憶・好感度を反映させたいとき）。"""
        processed = await self._services.engine.worker.run_until_idle(
            now=self._clock.now(), ignore_run_at=True, dedupe_keys=self._scope()
        )
        self.stats.jobs += processed
        await self._services.chat.pipeline.drain()
        return processed
