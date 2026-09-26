"""自発メッセージ（仕様 §7）。実装は service.ProactiveMessenger。

設定 API のサービスは settings.ProactiveSettingsService。

import するだけで MockLLM に `proactive_message` の決定的なハンドラが登録される（LLM_MODE=mock・評価ハーネス用）。
"""

from app.engine.proactive import mock as mock  # MockLLM へのハンドラの登録（副作用）
