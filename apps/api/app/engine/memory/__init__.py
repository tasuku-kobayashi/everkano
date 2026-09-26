"""Memory Engine（キャラクターエンジン v1.0 §4）。

公開するもの:
- `MemoryEngineService`: `app.engine.types.MemoryService` の実装（検索・分析・要約・約束）
- `MemoryEngineUnavailableError`: process_turns が一時的な障害で分析できなかったときの例外（ジョブを再実行する）
- `MemoryConfig`: 設定（`MemoryConfig.from_settings(settings)`）
- `UserMemoryService`: メモリパネル / 約束の API（routers/memories.py・routers/promises.py）
- `extract_call_name`: 関係性の記憶からユーザーの呼ばれたい名前を取り出す（Context Assembler 用）

import 時に MockLLM の `memory_analysis` / `memory_summary` を登録する（mock.py）。
"""

from __future__ import annotations

from app.engine.memory.config import MemoryConfig, RankingWeights
from app.engine.memory.mock import register_mock_handlers
from app.engine.memory.panel import UserMemoryService
from app.engine.memory.service import MemoryEngineService, MemoryEngineUnavailableError
from app.engine.memory.text import MODERATED_PLACEHOLDER, extract_call_name, sanitize_history

register_mock_handlers()

__all__ = [
    "MODERATED_PLACEHOLDER",
    "MemoryConfig",
    "MemoryEngineService",
    "MemoryEngineUnavailableError",
    "RankingWeights",
    "UserMemoryService",
    "extract_call_name",
    "sanitize_history",
]
