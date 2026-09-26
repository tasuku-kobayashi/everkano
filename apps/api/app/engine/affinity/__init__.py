"""好感度エンジン（仕様 §6）。実装は service.AffinityEngine。

import するだけで MockLLM に `affinity_eval` の決定的なハンドラが登録される（LLM_MODE=mock・評価ハーネス用）。
"""

from app.engine.affinity import mock as mock  # MockLLM へのハンドラの登録（副作用）
