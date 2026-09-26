"""自発メッセージの設定 API（E4: 停止・送らない時間帯）。

- 契約: Pydantic モデル（OpenAPI）が packages/shared/src/api.ts と一致する（コアの契約テストのパーサを使う）
- 統合: GET の既定値、PUT の部分更新・検証、キャラ別の設定、本人のスコープ（所有者）、監査ログ
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from app.core.db import create_pool
from app.core.errors import register_exception_handlers
from app.core.security import JwksCache, TokenVerifier
from app.routers import proactive
from app.services.audit import AuditLogger
from tests.conftest import World, make_settings
from tests.test_openapi_contract import API_TS, _compare_object, parse_api_ts

PROACTIVE_INTERFACES = (
    "ProactiveGlobalSettings",
    "ProactiveCharacterSetting",
    "ProactiveSettingsResponse",
    "UpdateProactiveGlobalSettingsRequest",
    "UpdateProactiveCharacterSettingRequest",
)


def router_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(proactive.router)
    return app


# ---------------------------------------------------------------------------
# 契約（DB 不要）
# ---------------------------------------------------------------------------


def test_models_match_api_ts() -> None:
    contract = parse_api_ts(API_TS.read_text(encoding="utf-8"))
    schema = router_app().openapi()
    components = schema["components"]["schemas"]
    for name in PROACTIVE_INTERFACES:
        assert name in contract.interfaces, name
        assert name in components, name
        _compare_object(name, contract.interfaces[name], name, schema, contract, is_request=name.endswith("Request"))
    # api.ts の `global` はそのままのキー名（Python の予約語だが別名で合わせる）
    assert "global" in components["ProactiveSettingsResponse"]["properties"]


def test_paths_and_auth() -> None:
    schema = router_app().openapi()
    paths = {(path, method) for path, ops in schema["paths"].items() for method in ops}
    assert paths == {
        ("/proactive/settings", "get"),
        ("/proactive/settings", "put"),
        ("/proactive/settings/{character_id}", "put"),
    }
    for path, method in paths:
        assert schema["paths"][path][method].get("security"), f"{method} {path} must require auth"
    for method in ("get", "put"):
        response = schema["paths"]["/proactive/settings"][method]["responses"]["200"]
        assert response["content"]["application/json"]["schema"]["$ref"].endswith("/ProactiveSettingsResponse")


def test_quiet_hours_bounds_in_schema() -> None:
    components = router_app().openapi()["components"]["schemas"]
    start = components["UpdateProactiveGlobalSettingsRequest"]["properties"]["quiet_start"]
    option = next(o for o in start["anyOf"] if o.get("type") == "integer")
    assert (option["minimum"], option["maximum"]) == (0, 23)


# ---------------------------------------------------------------------------
# 統合（ローカル DB）
# ---------------------------------------------------------------------------


@asynccontextmanager
async def settings_api(**overrides: Any) -> AsyncIterator[httpx.AsyncClient]:
    settings = make_settings(**overrides)
    pool = await create_pool(settings)
    jwks_http = httpx.AsyncClient()
    app = router_app()
    app.state.services = SimpleNamespace(
        settings=settings,
        pool=pool,
        audit=AuditLogger(pool),
        token_verifier=TokenVerifier(settings, JwksCache(settings.jwks_url, jwks_http)),
    )
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            yield client
    finally:
        await jwks_http.aclose()
        await pool.close()


@pytest.fixture
async def api() -> AsyncIterator[httpx.AsyncClient]:
    async with settings_api() as client:
        yield client


async def stored_quiet(world: World, user_id: uuid.UUID) -> tuple[int | None, int | None]:
    row = await world.conn.fetchrow(
        "select quiet_start, quiet_end from public.proactive_settings where user_id = $1 and character_id is null",
        user_id,
    )
    assert row is not None
    return row["quiet_start"], row["quiet_end"]


async def audit_payloads(world: World, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await world.conn.fetch(
        "select payload from public.audit_logs where user_id = $1 and event_type = 'proactive.settings_update'"
        " order by created_at, id",
        user_id,
    )
    return [r["payload"] for r in rows]


@pytest.mark.integration
async def test_defaults_and_partial_updates(world: World, api: httpx.AsyncClient) -> None:
    user = await world.create_user()
    res = await api.get("/proactive/settings", headers=user.headers)
    assert res.status_code == 200
    assert res.json() == {"global": {"enabled": True, "quiet_start": 0, "quiet_end": 7}, "characters": []}

    res = await api.put("/proactive/settings", json={"quiet_start": 22}, headers=user.headers)
    assert res.status_code == 200
    assert res.json()["global"] == {"enabled": True, "quiet_start": 22, "quiet_end": 7}

    res = await api.put("/proactive/settings", json={"enabled": False, "quiet_end": 8}, headers=user.headers)
    assert res.json()["global"] == {"enabled": False, "quiet_start": 22, "quiet_end": 8}

    # 省略・null は変更しない
    res = await api.put("/proactive/settings", json={"quiet_start": None}, headers=user.headers)
    assert res.json()["global"] == {"enabled": False, "quiet_start": 22, "quiet_end": 8}

    # 同じ時刻 = 制限なし（保存できる）
    res = await api.put("/proactive/settings", json={"quiet_start": 8}, headers=user.headers)
    assert res.json()["global"]["quiet_start"] == 8

    payloads = await audit_payloads(world, user.id)
    assert [p["changed"] for p in payloads] == [["quiet_start"], ["enabled", "quiet_end"], ["quiet_start"]]
    assert payloads[0]["scope"] == "global"
    assert payloads[1]["before"]["enabled"] is True
    assert payloads[1]["after"]["enabled"] is False
    rows = await world.conn.fetch("select character_id from public.proactive_settings where user_id = $1", user.id)
    assert [r["character_id"] for r in rows] == [None]  # 全体の設定は 1 行だけ
    # DB の値と API の値が一致する（片方だけ null の行を作らない）
    assert await stored_quiet(world, user.id) == (8, 8)


@pytest.mark.integration
async def test_single_bound_put_persists_a_coherent_pair(world: World, api: httpx.AsyncClient) -> None:
    """片方だけの PUT でも、もう片方を今の実効値で埋めて両方を保存する（GET と DB の値が一致する）。"""
    user = await world.create_user()
    res = await api.put("/proactive/settings", json={"quiet_end": 6}, headers=user.headers)
    assert res.json()["global"] == {"enabled": True, "quiet_start": 0, "quiet_end": 6}
    assert await stored_quiet(world, user.id) == (0, 6)

    other = await world.create_user()
    res = await api.put("/proactive/settings", json={"quiet_start": 23}, headers=other.headers)
    assert res.json()["global"] == {"enabled": True, "quiet_start": 23, "quiet_end": 7}
    assert await stored_quiet(world, other.id) == (23, 7)
    payload = (await audit_payloads(world, other.id))[0]
    assert payload["stored_before"] == {"enabled": True, "quiet_start": None, "quiet_end": None}
    assert payload["stored_after"] == {"enabled": True, "quiet_start": 23, "quiet_end": 7}
    assert payload["before"] == {"enabled": True, "quiet_start": 0, "quiet_end": 7}

    # オン・オフだけの変更では時間帯を書かない（両方 null = サーバーの既定に従う、のまま）
    third = await world.create_user()
    await api.put("/proactive/settings", json={"enabled": False}, headers=third.headers)
    assert await stored_quiet(world, third.id) == (None, None)
    res = await api.get("/proactive/settings", headers=third.headers)
    assert res.json()["global"] == {"enabled": False, "quiet_start": 0, "quiet_end": 7}


@pytest.mark.integration
async def test_defaults_come_from_engine_settings(world: World) -> None:
    """既定の時間帯は送信判定と同じ ENGINE_PROACTIVE_QUIET_START / END から取る。"""
    user = await world.create_user()
    async with settings_api(engine_proactive_quiet_start=1, engine_proactive_quiet_end=6) as api:
        res = await api.get("/proactive/settings", headers=user.headers)
        assert res.json()["global"] == {"enabled": True, "quiet_start": 1, "quiet_end": 6}
        res = await api.put("/proactive/settings", json={"quiet_start": 23}, headers=user.headers)
        assert res.json()["global"] == {"enabled": True, "quiet_start": 23, "quiet_end": 6}
    assert await stored_quiet(world, user.id) == (23, 6)


@pytest.mark.integration
async def test_db_rejects_a_single_null_bound(world: World) -> None:
    """DB の制約でも、片方だけ null の組（API と食い違う行）は作れない。"""
    user = await world.create_user()
    with pytest.raises(asyncpg.CheckViolationError):
        await world.conn.execute(
            "insert into public.proactive_settings (user_id, character_id, quiet_start) values ($1, null, 22)",
            user.id,
        )
    with pytest.raises(asyncpg.CheckViolationError):
        await world.conn.execute(
            "insert into public.proactive_settings (user_id, character_id, quiet_start, quiet_end)"
            " values ($1, $2, 22, 6)",
            user.id,
            world.character_id,
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "body",
    [
        {"quiet_start": 24},
        {"quiet_end": -1},
        {"quiet_start": "7"},
        {"quiet_start": 7.5},
        {"quiet_start": True},
        {"enabled": "false"},
        {"enabled": 0},
    ],
)
async def test_global_validation(world: World, api: httpx.AsyncClient, body: dict[str, Any]) -> None:
    user = await world.create_user()
    res = await api.put("/proactive/settings", json=body, headers=user.headers)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "validation_error"


@pytest.mark.integration
async def test_character_setting_and_ownership(world: World, api: httpx.AsyncClient) -> None:
    alice = await world.create_user()
    bob = await world.create_user()
    character = world.character_id
    res = await api.put(f"/proactive/settings/{character}", json={"enabled": False}, headers=alice.headers)
    assert res.status_code == 200
    assert res.json()["characters"] == [{"character_id": str(character), "enabled": False}]
    # 他のユーザーには見えない・影響しない
    res = await api.get("/proactive/settings", headers=bob.headers)
    assert res.json()["characters"] == []
    # 戻す
    res = await api.put(f"/proactive/settings/{character}", json={"enabled": True}, headers=alice.headers)
    assert res.json()["characters"] == [{"character_id": str(character), "enabled": True}]
    payloads = await audit_payloads(world, alice.id)
    assert [p["after"] for p in payloads] == [{"enabled": False}, {"enabled": True}]
    assert payloads[0]["scope"] == "character"
    assert await audit_payloads(world, bob.id) == []
    # 同じ値ならログを増やさない
    await api.put(f"/proactive/settings/{character}", json={"enabled": True}, headers=alice.headers)
    assert len(await audit_payloads(world, alice.id)) == 2


@pytest.mark.integration
async def test_character_not_found_and_validation(world: World, api: httpx.AsyncClient) -> None:
    user = await world.create_user()
    res = await api.put(f"/proactive/settings/{uuid.uuid4()}", json={"enabled": False}, headers=user.headers)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"
    inactive = await world.create_character(is_active=False)
    res = await api.put(f"/proactive/settings/{inactive}", json={"enabled": False}, headers=user.headers)
    assert res.status_code == 404
    res = await api.put(f"/proactive/settings/{world.character_id}", json={}, headers=user.headers)
    assert res.status_code == 422
    res = await api.put("/proactive/settings/not-a-uuid", json={"enabled": False}, headers=user.headers)
    assert res.status_code == 422


@pytest.mark.integration
async def test_requires_authentication(world: World, api: httpx.AsyncClient) -> None:
    assert (await api.get("/proactive/settings")).status_code == 401
    assert (await api.put("/proactive/settings", json={"enabled": False})).status_code == 401
    res = await api.get("/proactive/settings", headers={"Authorization": "Bearer nope"})
    assert res.status_code == 401
