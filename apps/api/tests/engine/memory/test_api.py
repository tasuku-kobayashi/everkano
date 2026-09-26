"""メモリパネル API（/memories の kind・履歴・墓標）と約束 API（/promises）の統合テスト。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from tests.conftest import World
from tests.engine.memory.conftest import Pair, make_pair, make_service, process

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 26, 3, 0, tzinfo=UTC)
MEMORY_FIELDS = {
    "id",
    "character_id",
    "content",
    "importance",
    "tags",
    "is_user_edited",
    "source_message_id",
    "created_at",
    "updated_at",
    "kind",
    "status",
    "superseded_by",
    "superseded_at",
    "last_referenced_at",
    "reference_count",
}
PROMISE_FIELDS = {"id", "character_id", "content", "due_at", "due_precision", "status", "created_at", "updated_at"}


async def test_memory_kind_and_superseded_history(api_client: httpx.AsyncClient, pool: Any, pair: Pair) -> None:
    character_id = str(pair.character_id)
    created = await api_client.post(
        "/memories",
        json={"character_id": character_id, "content": "朝はパン派", "kind": "preference"},
        headers=pair.headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body) == MEMORY_FIELDS
    assert body["kind"] == "preference"
    assert body["status"] == "active"
    assert body["superseded_by"] is None
    assert body["reference_count"] == 0
    assert body["is_user_edited"] is True

    default_kind = await api_client.post(
        "/memories", json={"character_id": character_id, "content": "犬を飼っている"}, headers=pair.headers
    )
    assert default_kind.json()["kind"] == "fact"

    # summary は種類として指定できない
    for payload in (
        {"character_id": character_id, "content": "x", "kind": "summary"},
        {"character_id": character_id, "content": "x", "kind": "unknown"},
    ):
        rejected = await api_client.post("/memories", json=payload, headers=pair.headers)
        assert rejected.status_code == 422, payload
        assert rejected.json()["error"]["code"] == "validation_error"
    patched = await api_client.patch(f"/memories/{body['id']}", json={"kind": "summary"}, headers=pair.headers)
    assert patched.status_code == 422
    changed = await api_client.patch(f"/memories/{body['id']}", json={"kind": "fact"}, headers=pair.headers)
    assert changed.status_code == 200
    assert changed.json()["kind"] == "fact"

    # 自動抽出の矛盾（転職）で置き換えた古い記憶は、include_superseded=true のときだけ返る
    service = make_service(pool)
    await process(service, pair, [await pair.turn("広告代理店で働いてるんだ", at=NOW)], NOW)
    later = NOW + timedelta(days=10)
    await process(service, pair, [await pair.turn("転職して、今は銀行で働いてるよ", at=later)], later)
    active = await api_client.get("/memories", params={"character_id": character_id}, headers=pair.headers)
    assert all(m["status"] == "active" for m in active.json()["memories"])
    assert not any("広告代理店" in m["content"] for m in active.json()["memories"])
    history = await api_client.get(
        "/memories", params={"character_id": character_id, "include_superseded": "true"}, headers=pair.headers
    )
    old = next(m for m in history.json()["memories"] if "広告代理店" in m["content"])
    assert old["status"] == "superseded"
    assert old["superseded_at"] is not None
    new = next(m for m in history.json()["memories"] if m["id"] == old["superseded_by"])
    assert "銀行" in new["content"]
    # 有効な記憶が先に並ぶ
    statuses = [m["status"] for m in history.json()["memories"]]
    assert statuses == sorted(statuses)  # "active" < "superseded"


async def test_delete_creates_tombstone_and_prevents_resurrection(
    api_client: httpx.AsyncClient, pool: Any, pair: Pair
) -> None:
    service = make_service(pool)
    await process(service, pair, [await pair.turn("来週の木曜、面接なんだよね", at=NOW)], NOW)
    [promise] = await pair.promises()
    memory_id = promise["source_memory_id"]
    assert memory_id is not None

    deleted = await api_client.delete(f"/memories/{memory_id}", headers=pair.headers)
    assert deleted.status_code == 204
    tombstones = await pair.world.conn.fetch(
        "select kind, content_hash, embedding is not null as has_embedding from public.memory_tombstones"
        " where user_id = $1",
        pair.user_id,
    )
    assert [(t["kind"], t["has_embedding"]) for t in tombstones] == [("promise", True)]
    # 削除した記憶から作った未達の約束は取り消す
    [promise] = await pair.promises()
    assert promise["status"] == "cancelled"
    assert promise["source_memory_id"] is None
    [delete_audit] = await pair.audit("memory.delete")
    assert delete_audit["source"] == "user"
    assert delete_audit["tombstone_id"] is not None
    changes = await pair.audit("promise.status_change")
    assert [(c["after"], c["source"]) for c in changes] == [("cancelled", "memory_deleted")]

    # 同じ話をもう一度しても、記憶も約束も作り直さない
    later = NOW + timedelta(hours=2)
    result = await process(service, pair, [await pair.turn("来週の木曜、面接なんだよね", at=later)], later)
    assert result.promises_created == ()
    assert result.skipped_tombstoned >= 1
    assert await pair.memories() == []
    assert len(await pair.promises()) == 1

    # 他人の記憶は削除できない・二重削除は 404
    assert (await api_client.delete(f"/memories/{memory_id}", headers=pair.headers)).status_code == 404


async def test_promises_list_and_update(api_client: httpx.AsyncClient, pool: Any, world: World) -> None:
    pair = await make_pair(world)
    stranger = await make_pair(world)
    service = make_service(pool)
    await process(service, pair, [await pair.turn("来週の木曜、面接なんだよね", at=NOW)], NOW)
    await process(service, pair, [await pair.turn("今度一緒にカラオケ行こうね", at=NOW + timedelta(minutes=1))], NOW)
    character_id = str(pair.character_id)

    listed = await api_client.get("/promises", params={"character_id": character_id}, headers=pair.headers)
    assert listed.status_code == 200, listed.text
    promises = listed.json()["promises"]
    assert [p["content"] for p in promises] == ["面接", "一緒にカラオケ行こう"]  # 期日の近い順、期日なしは最後
    assert all(set(p) == PROMISE_FIELDS for p in promises)
    interview, karaoke = promises
    assert interview["due_precision"] == "day"
    assert interview["due_at"].startswith("2026-10-01T03:00:00")
    assert karaoke["due_at"] is None
    assert karaoke["due_precision"] == "unknown"

    # 他人からは見えない・変更できない
    other = await api_client.get("/promises", params={"character_id": character_id}, headers=stranger.headers)
    assert other.json() == {"promises": []}
    forbidden = await api_client.patch(
        f"/promises/{interview['id']}", json={"status": "done"}, headers=stranger.headers
    )
    assert forbidden.status_code == 404
    missing = await api_client.patch(f"/promises/{uuid.uuid4()}", json={"status": "done"}, headers=pair.headers)
    assert missing.status_code == 404

    done = await api_client.patch(f"/promises/{interview['id']}", json={"status": "done"}, headers=pair.headers)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "done"
    again = await api_client.patch(f"/promises/{interview['id']}", json={"status": "done"}, headers=pair.headers)
    assert again.status_code == 200  # 同じ状態への変更は何もしない
    for bad in ({"status": "pending"}, {"status": "mentioned"}, {}):
        invalid = await api_client.patch(f"/promises/{karaoke['id']}", json=bad, headers=pair.headers)
        assert invalid.status_code == 422, bad

    cancelled = await api_client.patch(f"/promises/{karaoke['id']}", json={"status": "cancelled"}, headers=pair.headers)
    assert cancelled.json()["status"] == "cancelled"
    open_only = await api_client.get("/promises", params={"character_id": character_id}, headers=pair.headers)
    assert open_only.json()["promises"] == []
    closed = await api_client.get(
        "/promises", params={"character_id": character_id, "include_closed": "true"}, headers=pair.headers
    )
    assert {p["status"] for p in closed.json()["promises"]} == {"done", "cancelled"}

    changes = await pair.audit("promise.status_change")
    assert [(c["before"], c["after"], c["source"]) for c in changes] == [
        ("pending", "done", "user"),
        ("pending", "cancelled", "user"),
    ]
    assert all(c["request_id"] for c in changes)


async def test_cancelled_promise_cancels_its_calendar_event(
    api_client: httpx.AsyncClient, pool: Any, pair: Pair
) -> None:
    service = make_service(pool)
    await process(service, pair, [await pair.turn("来週の木曜、面接なんだよね", at=NOW)], NOW)
    [promise] = await pair.promises()
    event_id = await pair.world.conn.fetchval(
        """
        insert into public.character_events (character_id, kind, title, starts_at, ends_at, visibility, user_id,
                                             source)
        values ($1, 'promise', '面接', $2, $3, 'user', $4, 'promise') returning id
        """,
        pair.character_id,
        promise["due_at"],
        promise["due_at"] + timedelta(hours=1),
        pair.user_id,
    )
    await pair.world.conn.execute("update public.promises set event_id = $1 where id = $2", event_id, promise["id"])
    res = await api_client.patch(f"/promises/{promise['id']}", json={"status": "cancelled"}, headers=pair.headers)
    assert res.status_code == 200
    status = await pair.world.conn.fetchval("select status from public.character_events where id = $1", event_id)
    assert status == "cancelled"
    [change] = await pair.audit("promise.status_change")
    assert change["event_cancelled"] == str(event_id)


async def test_memories_are_scoped_to_the_owner(api_client: httpx.AsyncClient, world: World) -> None:
    alice = await make_pair(world)
    bob = await make_pair(world)
    created = await api_client.post(
        "/memories",
        json={"character_id": str(alice.character_id), "content": "猫を飼っている", "kind": "fact"},
        headers=alice.headers,
    )
    memory_id = created.json()["id"]
    for method, url, payload in (
        ("patch", f"/memories/{memory_id}", {"kind": "preference"}),
        ("delete", f"/memories/{memory_id}", None),
    ):
        res = await api_client.request(method.upper(), url, json=payload, headers=bob.headers)
        assert res.status_code == 404, (method, url)
    listed = await api_client.get(
        "/memories",
        params={"character_id": str(alice.character_id), "include_superseded": "true"},
        headers=bob.headers,
    )
    assert listed.json() == {"memories": []}
    tombstones = await world.conn.fetchval(
        "select count(*) from public.memory_tombstones where user_id = any($1::uuid[])", [alice.user_id, bob.user_id]
    )
    assert tombstones == 0
