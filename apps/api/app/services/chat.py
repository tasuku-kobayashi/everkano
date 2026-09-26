"""`POST /chat` と `POST /chat/stream` の入口（ENGINE_BRIEF §2.4）。処理本体は app/engine/pipeline.py。

- どちらも同じパイプライン（ChatPipeline）を**別タスク**で動かし、イベントをチャネル経由で受け取る。
  クライアントが接続を切っても（SSE の読み取りが止まっても）生成・保存は最後まで行われる（shield）。
- `/chat` はチャネルを最後まで読み、`done` の ChatResponse を返す（`error` は ApiError として送出）。
- `/chat/stream` はチャネルを SSE に変換する（app/engine/pipeline_sse.py）。
- 認証・所有者・レート制限のエラーはストリームを始める前に通常の JSON エラーとして返る（prepare が送出）。
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Final

from app.core.logging import get_logger
from app.core.security import CurrentUser
from app.engine.pipeline import ChatPipeline, ChatTurn, DoneEvent, ErrorEvent, PipelineEvent
from app.models.dm import ChatRequest, ChatResponse

logger = get_logger("chat")

_END: Final = object()


class PipelineChannel:
    """パイプラインのイベントを受け取るチャネル。読み手がいなくなっても書き手（生成タスク）は止まらない。"""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self.error: BaseException | None = None

    def put(self, event: PipelineEvent) -> None:
        self._queue.put_nowait(event)

    def close(self, error: BaseException | None = None) -> None:
        self.error = error
        self._queue.put_nowait(_END)

    async def next(self) -> PipelineEvent | None:
        """次のイベント。書き手が終わったら None（想定外の例外で終わった場合は self.error に入る）。"""
        item = await self._queue.get()
        if item is _END:
            self._queue.put_nowait(_END)  # 何度呼んでも None を返す
            return None
        event: PipelineEvent = item
        return event

    def __aiter__(self) -> PipelineChannel:
        return self

    async def __anext__(self) -> PipelineEvent:
        event = await self.next()
        if event is None:
            raise StopAsyncIteration
        return event


class ChatService:
    def __init__(self, *, pipeline: ChatPipeline) -> None:
        self._pipeline = pipeline
        self._inflight: set[asyncio.Task[None]] = set()

    @property
    def pipeline(self) -> ChatPipeline:
        return self._pipeline

    async def chat(self, user: CurrentUser, request: ChatRequest) -> ChatResponse:
        turn = await self._pipeline.prepare(user, request, streamed=False)
        channel = self.start(turn)
        async for event in channel:
            if isinstance(event, DoneEvent):
                return event.response
            if isinstance(event, ErrorEvent):
                raise event.to_api_error()
        if channel.error is not None:
            raise channel.error
        raise RuntimeError("chat pipeline ended without a result")  # pragma: no cover

    async def open_stream(self, user: CurrentUser, request: ChatRequest) -> PipelineChannel:
        """所有者を確認してから（404 はここで送出）パイプラインを開始し、イベントのチャネルを返す。"""
        turn = await self._pipeline.prepare(user, request, streamed=True)
        return self.start(turn)

    def start(self, turn: ChatTurn) -> PipelineChannel:
        channel = PipelineChannel()

        async def produce() -> None:
            try:
                await self._pipeline.run(turn, channel.put)
            except Exception as exc:
                logger.exception(
                    "chat pipeline failed",
                    extra={"fields": {"conversation_id": str(turn.conversation_id), "streamed": turn.streamed}},
                )
                channel.close(exc)
                return
            except BaseException as exc:  # 停止時の取り消し
                channel.close(exc)
                raise
            channel.close()

        task = asyncio.create_task(produce(), name=f"chat-pipeline-{turn.conversation_id}")
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)
        return channel

    async def drain(self, timeout_seconds: float = 20.0) -> None:
        """生成中の返答（クライアントが切断したものを含む）と返答後の処理の完了を待つ（停止時）。"""
        if self._inflight:
            _, pending = await asyncio.wait(set(self._inflight), timeout=timeout_seconds)
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await self._pipeline.drain(timeout_seconds)
