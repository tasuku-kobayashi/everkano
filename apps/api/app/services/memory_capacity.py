"""互換モジュール: 記憶の上限の実装は `app.engine.memory.capacity` に移った（キャラクターエンジン v1.0）。"""

from __future__ import annotations

from app.engine.memory.capacity import MAX_EVICTIONS_PER_INSERT, EvictedMemory, count_pair, lock_pair, make_room

__all__ = ["MAX_EVICTIONS_PER_INSERT", "EvictedMemory", "count_pair", "lock_pair", "make_room"]
