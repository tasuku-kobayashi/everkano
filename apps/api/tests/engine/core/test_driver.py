"""評価ハーネス用の EngineDriver: 時計を進めながらチャット・ジョブ・定期実行を決定的に動かす。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.container import EngineOverrides
from app.engine.pipeline_driver import EngineDriver
from app.engine.types import ManualClock
from tests.conftest import AppFactory, World, make_settings, services_of
from tests.engine.core.conftest import FakeAffinity, FakeCalendar, FakeProactive

pytestmark = pytest.mark.integration

T0 = datetime(2031, 6, 2, 0, 0, tzinfo=UTC)  # JST 09:00


async def test_driver_fast_forwards_chat_jobs_and_schedules(app_factory: AppFactory, world: World) -> None:
    clock = ManualClock(T0)
    proactive = FakeProactive()
    # 共有 DB の他のデータに触れないよう、定期実行のモジュールはフェイク（記憶は本物）
    overrides = EngineOverrides(calendar=FakeCalendar(), affinity=FakeAffinity(), proactive=proactive)
    namespace = f"test-{uuid.uuid4().hex[:8]}"  # 共有 DB の engine_schedules（本番・開発サーバーの記録）と分ける
    client = await app_factory(
        make_settings(engine_schedule_namespace=namespace), clock=clock, engine_overrides=overrides
    )
    services = services_of(client)
    user = await world.create_user()
    conversation = await client.post(
        "/conversations", json={"character_id": str(world.character_id)}, headers=user.headers
    )
    conversation_id = uuid.UUID(conversation.json()["conversation"]["id"])
    driver = EngineDriver(services, clock, job_scope=set())
    with pytest.raises(ValueError, match="ManualClock"):
        EngineDriver(services, ManualClock(T0))

    response = await driver.chat(user.id, world.character_id, conversation_id, "猫を飼ってるんだ。名前はミケ")
    assert response.user_message.created_at == T0 + timedelta(milliseconds=1)
    # 1 時間を 10 分ずつ進める: post_turn（既定 180 秒後）と定期実行（proactive.scan は 10 分ごと）
    await driver.advance(timedelta(hours=1), step=timedelta(minutes=10))
    assert clock.now() == T0 + timedelta(hours=1)
    assert driver.stats.jobs >= 1
    assert driver.stats.task_runs.get(f"{namespace}:proactive.scan", 0) >= 5
    assert proactive.scans[0] == T0 + timedelta(minutes=10)
    memories = await world.conn.fetchval("select count(*) from public.memories where user_id = $1", user.id)
    assert memories >= 1
    analyzed = await world.conn.fetchval(
        "select analyzed_until from public.conversations where id = $1", conversation_id
    )
    assert analyzed is not None
    # ターンの直後に反映したいときは settle()
    await driver.chat(user.id, world.character_id, conversation_id, "明日は友達と映画に行くよ")
    assert await driver.settle() >= 1
    names = await world.conn.fetch("select name from public.engine_schedules where name like $1", namespace + ":%")
    assert {r["name"] for r in names} >= {f"{namespace}:proactive.scan"}
    # jobs.cleanup（対象を絞れない）は早送りした時計では実行しない（共有 DB の他のジョブを壊さない）
    assert f"{namespace}:jobs.cleanup" not in {r["name"] for r in names}
    assert f"{namespace}:jobs.cleanup" not in driver.task_names
    assert f"{namespace}:jobs.cleanup" not in driver.stats.task_runs
    await world.conn.execute("delete from public.engine_schedules where name like $1", namespace + ":%")
    # 名前空間の無い（本番・開発サーバーの）記録には触れていない。名前空間付きの記録（同じ DB で並行して動く
    # 評価ハーネス・他のテストの "<namespace>:<task>"）は対象外にする
    assert (
        await world.conn.fetchval(
            "select count(*) from public.engine_schedules where next_run_at >= '2030-01-01' and name not like '%:%'"
        )
        == 0
    )


async def test_driver_with_real_modules_scoped_to_own_data(app_factory: AppFactory, world: World) -> None:
    """本物のモジュールで 2 日分を早送りする（定期実行は自分のキャラ・ユーザーだけに絞る）。"""
    clock = ManualClock(T0)
    namespace = f"test-{uuid.uuid4().hex[:8]}"
    client = await app_factory(make_settings(engine_schedule_namespace=namespace), clock=clock)
    services = services_of(client)
    user = await world.create_user()
    conversation = await client.post(
        "/conversations", json={"character_id": str(world.character_id)}, headers=user.headers
    )
    conversation_id = uuid.UUID(conversation.json()["conversation"]["id"])
    driver = EngineDriver(services, clock, job_scope=set(), character_ids=[world.character_id], user_ids=[user.id])
    try:
        await driver.chat(user.id, world.character_id, conversation_id, "来週の木曜に面接なんだ。緊張する")
        await driver.advance(timedelta(days=2), step=timedelta(minutes=30))
        assert driver.stats.task_runs.get(f"{namespace}:calendar.tick", 0) >= 90
        assert driver.stats.task_runs.get(f"{namespace}:affinity.daily", 0) >= 1
        # カレンダー: 自分のキャラの予定だけが生成・更新される
        events = await world.conn.fetchval(
            "select count(*) from public.character_events where character_id = $1", world.character_id
        )
        assert events > 0
        state = await world.conn.fetchrow(
            "select activity, updated_at from public.character_states where character_id = $1", world.character_id
        )
        assert state is not None
        # 返答後のジョブ: 記憶・約束・好感度
        assert await world.conn.fetchval("select count(*) from public.memories where user_id = $1", user.id) >= 1
        assert await world.conn.fetchval("select count(*) from public.promises where user_id = $1", user.id) >= 1
        assert await world.conn.fetchval("select count(*) from public.affinity_states where user_id = $1", user.id) == 1
        # 状態はチャットの文脈に反映される
        response = await driver.chat(user.id, world.character_id, conversation_id, "今なにしてる？")
        assert response.reply
        audit = await world.conn.fetchrow(
            "select payload from public.audit_logs where user_id = $1 and event_type = 'chat.response'"
            " order by id desc limit 1",
            user.id,
        )
        assert audit is not None
        assert audit["payload"]["state_used"] is not None
        assert audit["payload"]["stage_used"] in ("acquaintance", "friend", "close", "lover")
    finally:
        await world.conn.execute("delete from public.engine_schedules where name like $1", namespace + ":%")


async def test_driver_never_touches_other_sessions_jobs(app_factory: AppFactory, world: World) -> None:
    """早送りした時計（2031 年）で丸 2 日進めても、他のセッションの完了済み・実行中のジョブを消さない・戻さない。"""
    clock = ManualClock(T0)
    namespace = f"test-{uuid.uuid4().hex[:8]}"
    overrides = EngineOverrides(calendar=FakeCalendar(), affinity=FakeAffinity(), proactive=FakeProactive())
    client = await app_factory(
        make_settings(engine_schedule_namespace=namespace), clock=clock, engine_overrides=overrides
    )
    services = services_of(client)
    other = f"other-session-{uuid.uuid4().hex[:8]}"
    # 他のセッションのジョブ（いま動いている = running と、完了済み = done）。現在の実時間で作る
    running_id = await world.conn.fetchval(
        "insert into public.engine_jobs (kind, dedupe_key, payload, run_at, status, attempts, locked_at, locked_by)"
        " values ('post_turn', $1, '{}', now(), 'running', 1, now(), 'other-worker') returning id",
        other + "-running",
    )
    done_id = await world.conn.fetchval(
        "insert into public.engine_jobs (kind, dedupe_key, payload, run_at, status, attempts, finished_at)"
        " values ('post_turn', $1, '{}', now(), 'done', 1, now()) returning id",
        other + "-done",
    )
    driver = EngineDriver(services, clock, job_scope=set())
    try:
        await driver.advance(timedelta(days=2), step=timedelta(hours=1))
        rows = {
            r["id"]: r["status"]
            for r in await world.conn.fetch(
                "select id, status from public.engine_jobs where id = any($1::bigint[])", [running_id, done_id]
            )
        }
        assert rows == {running_id: "running", done_id: "done"}
    finally:
        await world.conn.execute("delete from public.engine_jobs where id = any($1::bigint[])", [running_id, done_id])
        await world.conn.execute("delete from public.engine_schedules where name like $1", namespace + ":%")
