from __future__ import annotations

from app.services.rate_limit import SlidingWindowRateLimiter


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
