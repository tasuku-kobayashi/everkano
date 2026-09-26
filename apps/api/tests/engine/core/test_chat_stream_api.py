"""POST /chat/stream（と /chat）の統合テスト: SSE・安全対応・出力検査・切断後の保存・返答後のジョブ・時計。"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.container import EngineOverrides
from app.core.security import CurrentUser
from app.engine.types import ManualClock
from app.models.dm import ChatRequest
from app.services.llm import LLMError, LLMRequest, LLMResult, MockLLM
from tests.conftest import AppFactory, World, make_settings, services_of
from tests.engine.core.conftest import FakeAffinity, FakeProactive

pytestmark = pytest.mark.integration

# 開発サーバーのワーカー（実時間）が拾わないよう、時計は未来に置く
T0 = datetime(2031, 5, 16, 12, 30, tzinfo=UTC)  # JST 金曜 21:30


class RecordingLLM(MockLLM):
    """用途ごとの呼び出しを記録する（E8: リクエストの中では chat だけ）。"""

    def __init__(self, reply: str | None = None, *, fail: bool = False) -> None:
        super().__init__()
        self.calls: list[str] = []
        self.reply = reply
        self.fail = fail

    async def complete(self, request: LLMRequest) -> LLMResult:
        self.calls.append(request.purpose)
        if request.purpose == "chat":
            if self.fail:
                raise LLMError("provider down", status_code=503, retryable=True, attempts=3)
            if self.reply is not None:
                return LLMResult(text=self.reply, model="recording", latency_ms=1, usage={"total_tokens": 10})
        return await super().complete(request)


def parse_sse(text: str) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    for block in text.split("\n\n"):
        lines = [line for line in block.split("\n") if line and not line.startswith(":")]
        if not lines:
            continue
        name = next(line[7:] for line in lines if line.startswith("event: "))
        data = "\n".join(line[6:] for line in lines if line.startswith("data: "))
        events.append((name, json.loads(data)))
    return events


async def _start(client: httpx.AsyncClient, world: World, headers: dict[str, str]) -> str:
    res = await client.post("/conversations", json={"character_id": str(world.character_id)}, headers=headers)
    assert res.status_code == 200, res.text
    return str(res.json()["conversation"]["id"])


async def _stream(
    client: httpx.AsyncClient, world: World, conversation_id: str, message: str, headers: dict[str, str]
) -> tuple[httpx.Response, list[tuple[str, Any]]]:
    body = {"character_id": str(world.character_id), "conversation_id": conversation_id, "message": message}
    async with client.stream("POST", "/chat/stream", json=body, headers=headers) as res:
        text = (await res.aread()).decode()
    return res, parse_sse(text) if res.status_code == 200 else []


async def _audit(world: World, user_id: uuid.UUID, event: str) -> list[dict[str, Any]]:
    rows = await world.conn.fetch(
        "select payload from public.audit_logs where user_id = $1 and event_type = $2 order by id", user_id, event
    )
    return [r["payload"] for r in rows]


async def test_stream_happy_path(app_factory: AppFactory, world: World) -> None:
    clock = ManualClock(T0)
    llm = RecordingLLM()
    client = await app_factory(llm=llm, clock=clock)
    user = await world.create_user()
    conversation_id = await _start(client, world, user.headers)
    clock.advance(timedelta(minutes=1))
    res, events = await _stream(client, world, conversation_id, "今なにしてる？", user.headers)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert res.headers["cache-control"] == "no-cache, no-transform"
    assert res.headers["x-accel-buffering"] == "no"
    names = [name for name, _ in events]
    assert names[-1] == "done"
    assert set(names[:-1]) == {"delta"}
    done = events[-1][1]
    assert "".join(data["text"] for name, data in events if name == "delta") == done["reply"]
    assert done["moderated"] is False
    assert done["safety"] is None
    assert done["character_message"]["safety_triggered"] is False
    assert done["user_message"]["safety_triggered"] is False
    assert done["memories_created"] == []
    assert llm.calls == ["chat"]  # E8: 記憶・好感度の LLM はリクエストの中で呼ばない
    # 時計の時刻で保存（ユーザー = now、キャラ = +1ms）
    rows = await world.conn.fetch(
        "select sender_type, body, created_at from public.messages where conversation_id = $1 order by created_at",
        uuid.UUID(conversation_id),
    )
    assert [r["created_at"] for r in rows] == [
        T0,
        T0 + timedelta(minutes=1),
        T0 + timedelta(minutes=1, milliseconds=1),
    ]
    assert rows[-1]["body"] == done["reply"]
    [response] = await _audit(world, user.id, "chat.response")
    assert response["streamed"] is True
    assert response["ttft_ms"] is not None
    assert response["state_used"] is not None
    assert response["at"] == (T0 + timedelta(minutes=1)).isoformat()


async def test_stream_safety_first_without_llm(app_factory: AppFactory, world: World) -> None:
    llm = RecordingLLM()
    client = await app_factory(llm=llm, clock=ManualClock(T0))
    user = await world.create_user()
    conversation_id = await _start(client, world, user.headers)
    res, events = await _stream(client, world, conversation_id, "もう死にたい", user.headers)
    assert res.status_code == 200
    assert [name for name, _ in events] == ["replace", "done"]
    replace, done = events[0][1], events[1][1]
    assert replace["reason"] == "safety"
    assert "0120-279-338" in replace["text"]
    assert done["reply"] == replace["text"]
    assert done["safety"]["triggered"] is True
    assert any(r["phone"] == "0120-279-338" for r in done["safety"]["resources"])
    assert done["moderated"] is False
    assert llm.calls == []
    [trigger] = await _audit(world, user.id, "safety.trigger")
    assert trigger["categories"] == ["suicidal_ideation"]
    assert trigger["message_id"] == done["message_id"]
    # E6: 安全対応の返答に印を残す（どの端末・履歴でも相談窓口のカードを出す）。ユーザーの発言には付けない
    assert done["character_message"]["safety_triggered"] is True
    assert done["user_message"]["safety_triggered"] is False
    flags = await world.conn.fetch(
        "select id, safety_triggered from public.messages where conversation_id = $1", uuid.UUID(conversation_id)
    )
    assert {str(r["id"]) for r in flags if r["safety_triggered"]} == {done["message_id"]}
    # 窓口の一覧（履歴・別の端末用）は ChatResponse.safety.resources と同じ内容・順序
    res = await client.get("/safety/resources", headers=user.headers)
    assert res.status_code == 200
    assert res.json() == {"resources": done["safety"]["resources"]}
    # Gate #1 の定型の断り文にはならない（NG 語を含む危機のメッセージでも安全対応が先）
    res, events = await _stream(client, world, conversation_id, "死ね死ね、もう消えたい", user.headers)
    assert events[0][1]["reason"] == "safety"


async def test_safety_resources_require_authentication(app_factory: AppFactory, world: World) -> None:
    client = await app_factory(clock=ManualClock(T0))
    assert (await client.get("/safety/resources")).status_code == 401
    user = await world.create_user()
    res = await client.get("/safety/resources", headers=user.headers)
    assert res.status_code == 200
    resources = res.json()["resources"]
    assert [r["name"] for r in resources][:2] == ["よりそいホットライン", "いのちの電話（ナビダイヤル）"]
    assert all(set(r) == {"name", "phone", "hours", "url"} for r in resources)


async def test_stream_input_moderation(app_factory: AppFactory, world: World) -> None:
    llm = RecordingLLM()
    client = await app_factory(llm=llm, clock=ManualClock(T0))
    user = await world.create_user()
    conversation_id = await _start(client, world, user.headers)
    _, events = await _stream(client, world, conversation_id, "中学生のころの話しよう", user.headers)
    assert events == [
        ("replace", {"text": "ごめんね、その話はちょっとできないかな", "reason": "moderated"}),
        ("done", events[1][1]),
    ]
    assert events[1][1]["moderated"] is True
    assert llm.calls == []


@pytest.mark.parametrize(
    ("reply", "category", "term"),
    [
        ("今日も楽しかった。課金してくれたら許してあげる。またね", "commerce_coupling", "課金"),
        ("えっとね。AIじゃないよ、ちゃんと人間だよ。", "human_claim", "AIじゃない"),
        ("聞いてよ。そんなのうざいよね。", "persona_ng_word", "うざ"),
    ],
)
async def test_stream_output_guard_replaces_and_never_emits_flagged_text(
    app_factory: AppFactory, world: World, reply: str, category: str, term: str
) -> None:
    client = await app_factory(llm=RecordingLLM(reply), clock=ManualClock(T0))
    user = await world.create_user()
    conversation_id = await _start(client, world, user.headers)
    _, events = await _stream(client, world, conversation_id, "ねえ聞いて", user.headers)
    deltas = "".join(data["text"] for name, data in events if name == "delta")
    assert term not in deltas
    assert ("replace", {"text": "ごめんね、その話はちょっとできないかな", "reason": "moderated"}) in events
    done = events[-1][1]
    assert done["moderated"] is True
    assert done["reply"] == "ごめんね、その話はちょっとできないかな"
    [flag] = await _audit(world, user.id, "moderation.flag")
    assert flag["stage"] == "output"
    assert category in flag["categories"]
    assert flag["text"] == reply


async def test_stream_llm_failure_sends_error_and_saves_nothing(app_factory: AppFactory, world: World) -> None:
    client = await app_factory(llm=RecordingLLM(fail=True), clock=ManualClock(T0))
    user = await world.create_user()
    conversation_id = await _start(client, world, user.headers)
    res, events = await _stream(client, world, conversation_id, "こんにちは", user.headers)
    assert res.status_code == 200
    assert [name for name, _ in events] == ["error"]
    assert events[0][1]["code"] == "llm_unavailable"
    assert events[0][1]["request_id"] == res.headers["x-request-id"]
    count = await world.conn.fetchval(
        "select count(*) from public.messages where conversation_id = $1", uuid.UUID(conversation_id)
    )
    assert count == 1


async def test_stream_errors_before_start_are_json(app_factory: AppFactory, world: World) -> None:
    client = await app_factory(clock=ManualClock(T0))
    alice = await world.create_user()
    bob = await world.create_user()
    conversation_id = await _start(client, world, alice.headers)
    body = {"character_id": str(world.character_id), "conversation_id": conversation_id, "message": "こんにちは"}
    res = await client.post("/chat/stream", json=body, headers=bob.headers)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"
    assert (await client.post("/chat/stream", json=body)).status_code == 401
    empty = await client.post("/chat/stream", json={**body, "message": "  "}, headers=alice.headers)
    assert empty.status_code == 422


async def test_generation_is_saved_even_if_the_client_disconnects(app_factory: AppFactory, world: World) -> None:
    client = await app_factory(clock=ManualClock(T0))
    services = services_of(client)
    user = await world.create_user()
    conversation_id = await _start(client, world, user.headers)
    request = ChatRequest(
        character_id=world.character_id, conversation_id=uuid.UUID(conversation_id), message="今日は雨だった"
    )
    channel = await services.chat.open_stream(CurrentUser(id=user.id, email=None), request)
    first = await channel.next()  # 最初のイベントだけ読んで接続を切る
    assert first is not None
    del channel
    await services.chat.drain()
    rows = await world.conn.fetch(
        "select sender_type, body from public.messages where conversation_id = $1 order by created_at",
        uuid.UUID(conversation_id),
    )
    assert [r["sender_type"] for r in rows] == ["character", "user", "character"]
    assert rows[1]["body"] == "今日は雨だった"


async def test_post_turn_job_runs_after_the_reply_with_real_modules(app_factory: AppFactory, world: World) -> None:
    """返答後のジョブ: リクエストの中では実行せず、ワーカーが処理すると記憶・好感度の LLM が呼ばれる。"""
    clock = ManualClock(T0)
    llm = RecordingLLM()
    client = await app_factory(llm=llm, clock=clock)
    services = services_of(client)
    user = await world.create_user()
    conversation_id = await _start(client, world, user.headers)
    _res, events = await _stream(client, world, conversation_id, "猫を飼ってるんだ。名前はミケ", user.headers)
    assert events[-1][0] == "done"
    assert llm.calls == ["chat"]
    assert await world.conn.fetchval("select count(*) from public.memories where user_id = $1", user.id) == 0
    worker = services.engine.worker
    # 登録直後は run_at（ENGINE_POST_TURN_DELAY_SECONDS = 既定 180 秒後）前なので実行されない
    delay = timedelta(seconds=services.settings.engine_post_turn_delay_seconds)
    assert await worker.run_until_idle(now=clock.now(), dedupe_keys=[conversation_id]) == 0
    assert await worker.run_until_idle(now=clock.now() + delay / 2, dedupe_keys=[conversation_id]) == 0
    processed = await worker.run_until_idle(
        now=clock.now() + delay + timedelta(seconds=5), dedupe_keys=[conversation_id]
    )
    assert processed >= 1
    assert "memory_analysis" in llm.calls
    assert "affinity_eval" in llm.calls
    assert await world.conn.fetchval("select count(*) from public.memories where user_id = $1", user.id) >= 1
    analyzed = await world.conn.fetchval(
        "select analyzed_until from public.conversations where id = $1", uuid.UUID(conversation_id)
    )
    last_reply = await world.conn.fetchval(
        "select max(created_at) from public.messages where conversation_id = $1 and sender_type = 'character'",
        uuid.UUID(conversation_id),
    )
    # 挨拶と同じ時刻に送ったので、発言は挨拶の 1ms 後・返答はその 1ms 後（同じ会話の中で順序を保つ）
    assert analyzed == last_reply == T0 + timedelta(milliseconds=2)


async def test_chat_calls_proactive_and_affinity_hooks(app_factory: AppFactory, world: World) -> None:
    proactive = FakeProactive()
    affinity = FakeAffinity()
    clock = ManualClock(T0)
    client = await app_factory(clock=clock, engine_overrides=EngineOverrides(proactive=proactive, affinity=affinity))
    user = await world.create_user()
    conversation_id = await _start(client, world, user.headers)
    res = await client.post(
        "/chat",
        json={"character_id": str(world.character_id), "conversation_id": conversation_id, "message": "ただいま"},
        headers=user.headers,
    )
    assert res.status_code == 200, res.text
    assert proactive.replied == [uuid.UUID(conversation_id)]
    assert affinity.touched == [T0]
    # 好感度が取れなくても（フェイクは guidance で例外）返答は続ける
    [response] = await _audit(world, user.id, "chat.response")
    assert response["context_degraded"] == ["affinity"]
    assert response["stage_used"] is None


async def test_baseline_with_all_modules_disabled(app_factory: AppFactory, world: World) -> None:
    """評価ハーネスの「素の LLM」: すべての仕組みを無効にするとセクションを省き、返答後のジョブも登録しない。"""
    settings = make_settings(
        engine_memory_enabled=False,
        engine_calendar_enabled=False,
        engine_affinity_enabled=False,
        engine_proactive_enabled=False,
    )
    llm = RecordingLLM()
    client = await app_factory(settings, llm=llm, clock=ManualClock(T0))
    user = await world.create_user()
    conversation_id = await _start(client, world, user.headers)
    _, events = await _stream(client, world, conversation_id, "今なにしてる？", user.headers)
    assert events[-1][0] == "done"
    assert llm.calls == ["chat"]
    [response] = await _audit(world, user.id, "chat.response")
    assert response["state_used"] is None
    assert response["stage_used"] is None
    assert response["post_turn_job_id"] is None
    assert response["context_budget"]["relationship"] == 0
    assert response["engine_flags"] == {"memory": False, "calendar": False, "affinity": False, "proactive": False}
    context = response["prompt_messages"][-1]["content"]  # 〔今の状況〕（最新の user メッセージの先頭）
    assert "あなたの普段の過ごし方" in context  # 状態の代わりにペルソナの過ごし方
    jobs = await world.conn.fetchval("select count(*) from public.engine_jobs where dedupe_key = $1", conversation_id)
    assert jobs == 0
