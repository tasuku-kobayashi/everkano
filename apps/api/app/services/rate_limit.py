"""プロセス内のスライディングウィンドウ・レート制限（ユーザー × バケット単位）。

注意: プロセス内メモリで保持するため、API を複数プロセス/複数マシンで動かす場合は
それぞれが独立にカウントする（MVP の割り切り。将来は Redis 等に置き換える）。
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

_CLEANUP_EVERY: Final[int] = 1000


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0
    remaining: int = 0


class SlidingWindowRateLimiter:
    def __init__(
        self,
        limits: dict[str, int],
        *,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limits = dict(limits)
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._calls = 0

    def check(self, bucket: str, key: str) -> RateLimitDecision:
        """1回分を消費できれば記録して allowed=True を返す。"""
        limit = self._limits[bucket]
        now = self._clock()
        hits = self._hits.setdefault((bucket, key), deque())
        cutoff = now - self._window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        self._maybe_cleanup(cutoff)
        if len(hits) >= limit:
            retry_after = max(1, math.ceil(hits[0] + self._window - now))
            return RateLimitDecision(allowed=False, retry_after_seconds=retry_after, remaining=0)
        hits.append(now)
        return RateLimitDecision(allowed=True, remaining=limit - len(hits))

    def _maybe_cleanup(self, cutoff: float) -> None:
        self._calls += 1
        if self._calls % _CLEANUP_EVERY:
            return
        stale = [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]
        for k in stale:
            del self._hits[k]
