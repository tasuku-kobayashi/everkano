"""mock の費用見積もりに使うトークナイザ（任意）。

mock では LLM の usage が無いので、既定では「文字数 × --tokens-per-char」でトークン数を見積もる。本番のモデルの
トークナイザ（Hugging Face の `tokenizer.json`）を `--tokenizer PATH` で渡すと、実際のプロンプト・出力の本文を
そのトークナイザで数える（日本語・JSON・英字の混在の違いを反映する）。

- DeepSeek-V3（V3 / R1 / V3.1 で共通の 128K 語彙）の tokenizer.json は、PyPI の `deepseek-tokenizer`（0.1.3〜0.2.0）
  に同梱されている（deepseek-ai/DeepSeek-V3 の配布物と同じ語彙数 128,000 + 追加トークン 818）。
- 依存を増やさないため `tokenizers` は実行時に読み込む（プロジェクトの依存には入れない）:
      uv run --with tokenizers python -m evals.run --tokenizer /path/to/tokenizer.json ...
- チャットのテンプレートが付ける役割のトークン（<｜User｜> など）は 1 メッセージあたり MESSAGE_OVERHEAD_TOKENS。
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

MESSAGE_OVERHEAD_TOKENS: Final[int] = 2

TokenCounter = Callable[[str], int]


class TokenizerUnavailableError(RuntimeError):
    """`tokenizers` が入っていない・tokenizer.json が読めない。"""


def load_token_counter(path: Path) -> TokenCounter:
    try:
        module: Any = importlib.import_module("tokenizers")
    except ImportError as exc:  # pragma: no cover - 実行環境による
        raise TokenizerUnavailableError(
            "--tokenizer には `tokenizers` が必要です（uv run --with tokenizers python -m evals.run ...）"
        ) from exc
    try:
        tokenizer = module.Tokenizer.from_file(str(path))
    except Exception as exc:
        raise TokenizerUnavailableError(f"{path}: tokenizer.json を読めません: {exc}") from exc

    def count(text: str) -> int:
        if not text:
            return 0
        return len(tokenizer.encode(text, add_special_tokens=False).ids)

    return count
