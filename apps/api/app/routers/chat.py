from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.container import ChatRateLimitedUser, ServicesDep
from app.engine.pipeline_sse import SSE_HEADERS, SSE_MEDIA_TYPE, sse_stream
from app.models.common import ERROR_RESPONSES, LLM_UNAVAILABLE_RESPONSE, NOT_FOUND_RESPONSE
from app.models.dm import ChatRequest, ChatResponse

router = APIRouter(tags=["dm"])

_PIPELINE_DESCRIPTION = (
    "E6（自傷・希死念慮）の安全対応を最初に判定し、ユーザー発言を Gate #1 で検証したうえで、"
    "Context Assembler（短期履歴・記憶・キャラの状態・ふたりの関係・約束）からプロンプトを組み立てて返答を生成する。"
    "返答は送る前に Gate #1・キャラの NG ワード・E2（購入と関係を結びつける発言の禁止）・"
    "E3（実在の人間だという主張の禁止）で検査する。"
    "ユーザー発言とキャラ返答を保存し、記憶の抽出・約束の予定化・好感度の評価は返答の後に非同期で行う"
    "（memories_created は常に空。新しい記憶は Realtime で届く）。"
)


@router.post(
    "/chat",
    summary="DM のキャラ返答を生成",
    description=_PIPELINE_DESCRIPTION,
    responses={**ERROR_RESPONSES, **NOT_FOUND_RESPONSE, **LLM_UNAVAILABLE_RESPONSE},
)
async def chat(body: ChatRequest, user: ChatRateLimitedUser, services: ServicesDep) -> ChatResponse:
    return await services.chat.chat(user, body)


@router.post(
    "/chat/stream",
    summary="DM のキャラ返答をストリーミングで生成（Server-Sent Events）",
    description=(
        _PIPELINE_DESCRIPTION
        + "\n\nレスポンスは `text/event-stream`。イベントは `delta`（{text}: 検査を通過した続きの本文）/ "
        "`replace`（{text, reason: moderated | safety}: 表示中の本文を置き換える）/ "
        "`done`（ChatResponse: 保存済みの結果）/ `error`（{code, message, request_id}: 何も保存していない）。"
        "イベントの間は `: keep-alive` コメントが届く。認証・所有者・レート制限・入力のエラーは"
        "ストリームを始める前に通常の JSON エラーで返る。クライアントが接続を切っても返答は生成・保存される。"
    ),
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Server-Sent Events（ChatStreamEvent）",
            "content": {SSE_MEDIA_TYPE: {"schema": {"type": "string"}}},
        },
        **ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def chat_stream(body: ChatRequest, user: ChatRateLimitedUser, services: ServicesDep) -> StreamingResponse:
    channel = await services.chat.open_stream(user, body)
    return StreamingResponse(
        sse_stream(channel, heartbeat_seconds=services.settings.chat_stream_heartbeat_seconds),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )
