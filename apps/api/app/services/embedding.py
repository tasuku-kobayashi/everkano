"""互換モジュール: 埋め込みの実装は `app.engine.memory.embedding` に移った（キャラクターエンジン v1.0）。

コンテナ（app/container.py）・main.py・チャット・テストがこのパスを参照しているため、公開名を再エクスポートする。
新しいコードは `app.engine.memory.embedding` を直接 import すること。
"""

from __future__ import annotations

from app.engine.memory.embedding import (
    EmbeddingClient,
    EmbeddingError,
    HashEmbedding,
    OpenAICompatibleEmbedding,
    cosine_similarity,
    create_embedding_client,
    embedding_failure_payload,
)

__all__ = [
    "EmbeddingClient",
    "EmbeddingError",
    "HashEmbedding",
    "OpenAICompatibleEmbedding",
    "cosine_similarity",
    "create_embedding_client",
    "embedding_failure_payload",
]
