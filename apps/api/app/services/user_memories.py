"""互換モジュール: メモリパネルの実装は `app.engine.memory.panel` に移った（コンテナが参照している）。"""

from __future__ import annotations

from app.engine.memory.panel import USER_MEMORY_EMBED_DEADLINE_SECONDS, UserMemoryService

__all__ = ["USER_MEMORY_EMBED_DEADLINE_SECONDS", "UserMemoryService"]
