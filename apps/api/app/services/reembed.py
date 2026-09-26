"""互換モジュール: 再埋め込みの実装は `app.engine.memory.reembed` に移った（scripts/reembed_memories.py が参照）。"""

from __future__ import annotations

from app.engine.memory.reembed import ReembedStats, reembed_character_memories, reembed_memories

__all__ = ["ReembedStats", "reembed_character_memories", "reembed_memories"]
