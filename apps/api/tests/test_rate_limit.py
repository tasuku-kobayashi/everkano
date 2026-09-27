from __future__ import annotations

from typing import Any

from app.container import RateLimit, rate_limit_buckets
from app.main import create_app
from app.services.rate_limit import SlidingWindowRateLimiter
from tests.conftest import make_settings


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_sliding_window() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter({"chat": 2}, window_seconds=60, clock=clock)
    assert limiter.check("chat", "u1").allowed
    clock.now += 10
    assert limiter.check("chat", "u1").allowed
    denied = limiter.check("chat", "u1")
    assert not denied.allowed
    assert denied.retry_after_seconds == 50
    # 別ユーザー・別バケットは独立
    assert limiter.check("chat", "u2").allowed
    clock.now += 50.5  # 最初のリクエストがウィンドウ外に
    assert limiter.check("chat", "u1").allowed
    assert not limiter.check("chat", "u1").allowed


def test_retry_after_is_at_least_one_second() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter({"b": 1}, window_seconds=60, clock=clock)
    assert limiter.check("b", "u").allowed
    clock.now += 59.9
    assert limiter.check("b", "u").retry_after_seconds == 1


# ---------------------------------------------------------------------------
# ルーターの書き込みはすべてレート制限のバケットを持つ（読み取りは持たない）
# ---------------------------------------------------------------------------


def _routes(app: Any) -> list[Any]:
    """include_router で足したルーター（_IncludedRouter）の中の APIRoute も含めて平らに返す。"""
    found: list[Any] = []
    for route in app.routes:
        router = getattr(route, "original_router", None)
        found.extend(router.routes if router is not None else [route])
    return found


def _bucket(app: Any, path: str, method: str) -> str | None:
    for route in _routes(app):
        if getattr(route, "path", None) != path or method not in (getattr(route, "methods", None) or set()):
            continue
        for dependency in route.dependant.dependencies:
            if isinstance(dependency.call, RateLimit):
                return dependency.call.bucket
        return None
    raise AssertionError(f"route not found: {method} {path}")


def test_every_write_route_is_rate_limited() -> None:
    app = create_app(make_settings())
    expected = {
        ("POST", "/chat"): "chat",
        ("POST", "/chat/stream"): "chat",
        ("POST", "/comments"): "comments",
        ("POST", "/comments/generate"): "comments",
        ("POST", "/memories"): "memories",
        ("PATCH", "/memories/{memory_id}"): "memories",
        ("DELETE", "/memories/{memory_id}"): "memories",
        ("PATCH", "/promises/{promise_id}"): "memories",
        ("PUT", "/proactive/settings"): "settings",
        ("PUT", "/proactive/settings/{character_id}"): "settings",
    }
    for (method, path), bucket in expected.items():
        assert _bucket(app, path, method) == bucket, f"{method} {path}"
    for method, path in [
        ("GET", "/memories"),
        ("GET", "/promises"),
        ("GET", "/proactive/settings"),
        ("GET", "/safety/resources"),
        ("POST", "/conversations"),
    ]:
        assert _bucket(app, path, method) is None, f"{method} {path}"
    # ルーターが使うバケットはすべて設定（rate_limit_*_per_minute）にある
    assert set(expected.values()) <= set(rate_limit_buckets(make_settings()))
