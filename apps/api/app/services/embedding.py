"""テキスト埋め込み（長期メモリ用）。

- `EMBEDDING_MODE=live` : OpenAI 互換 `/embeddings` API（既定 text-embedding-3-small, 1536次元）
- `EMBEDDING_MODE=hash` : 外部APIを呼ばない決定的な埋め込み。文字 1〜3-gram を hashlib で
  1536 次元に符号付きハッシュし L2 正規化する（開発・テスト用。語彙の重なりを捉える程度の精度）。
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import random
import re
import time
import unicodedata
from collections.abc import Sequence
from typing import Final, Protocol

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger("embedding")


class EmbeddingError(Exception):
    """埋め込み生成に失敗した（リトライ後）。"""


class EmbeddingClient(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# n-gram ごとの重み（1文字は共有が多いので軽く、2文字を重視）
_NGRAM_WEIGHTS: Final[dict[int, float]] = {1: 0.5, 2: 1.0, 3: 0.5}
# ひらがなのみの n-gram（助詞・語尾など）はほぼ機能語なので大きく減衰させる
_HIRAGANA_ONLY_WEIGHT: Final[float] = 0.1
# 漢字・カタカナ・英数字の連続（≒内容語）をそのまま1特徴として加える重み
_CONTENT_RUN_WEIGHT: Final[float] = 2.0
_HIRAGANA_ONLY: Final = re.compile(r"^[\u3040-\u309f]+$")
_CONTENT_RUN: Final = re.compile(r"[\u4e00-\u9fff\u3005\u30a0-\u30ffa-z0-9]+")


def _normalize_for_embedding(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).lower()
    # 空白・記号・制御文字を除去（内容語の n-gram に集中させる）
    return "".join(ch for ch in value if unicodedata.category(ch)[0] not in {"Z", "P", "S", "C"})


def _hash_features(text: str) -> list[tuple[str, float]]:
    features: list[tuple[str, float]] = []
    normalized = _normalize_for_embedding(text)
    for n, weight in _NGRAM_WEIGHTS.items():
        for i in range(len(normalized) - n + 1):
            gram = normalized[i : i + n]
            w = weight * (_HIRAGANA_ONLY_WEIGHT if _HIRAGANA_ONLY.match(gram) else 1.0)
            features.append((f"g:{gram}", w))
    # 内容語は句読点で区切られた状態で抽出する（記号除去後だと「来週、大阪」が連結してしまう）
    for run in _CONTENT_RUN.findall(unicodedata.normalize("NFKC", text).lower()):
        features.append((f"w:{run}", _CONTENT_RUN_WEIGHT))
    return features


class HashEmbedding:
    """決定的な特徴ハッシュ埋め込み（Python の hash() はプロセス毎に変わるため hashlib を使う）。

    特徴 = 文字 1〜3-gram（ひらがなのみの gram は減衰）+ 内容語（漢字/カタカナ/英数字の連続）。
    各特徴を blake2b で次元と符号にハッシュして加算し、L2 正規化する。
    """

    def __init__(self, dimensions: int = 1536) -> None:
        self._dimensions = dimensions

    @property
    def model_name(self) -> str:
        return f"hash-ngram-{self._dimensions}"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self._dimensions
        for feature, weight in _hash_features(text):
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            h = int.from_bytes(digest, "big")
            index = h % self._dimensions
            sign = 1.0 if (h >> 63) & 1 else -1.0
            vec[index] += sign * weight
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            # 空文字など: ゼロベクトルは pgvector のコサイン距離で NaN になるため固定の単位ベクトル
            vec[0] = 1.0
            return vec
        return [v / norm for v in vec]

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]


class OpenAICompatibleEmbedding:
    """OpenAI 互換 `/embeddings` API クライアント（リトライ付き）。"""

    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient,
        *,
        backoff_base_seconds: float = 0.5,
    ) -> None:
        if settings.embedding_api_key is None:
            raise ValueError("EMBEDDING_API_KEY is required for live embeddings")
        self._url = f"{settings.embedding_base_url}/embeddings"
        self._api_key = settings.embedding_api_key.get_secret_value()
        self._model = settings.embedding_model
        self._dimensions = settings.embedding_dimensions
        self._timeout = settings.llm_timeout_seconds
        self._max_retries = settings.llm_max_retries
        self._backoff = backoff_base_seconds
        self._http = http

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        payload: dict[str, object] = {"model": self._model, "input": list(texts)}
        if self._model.startswith("text-embedding-3"):
            payload["dimensions"] = self._dimensions
        attempt = 0
        started = time.perf_counter()
        while True:
            try:
                response = await self._http.post(
                    self._url,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=self._timeout,
                )
            except httpx.TransportError as exc:
                error: Exception = exc
                retryable = True
            else:
                if response.status_code == 200:
                    return self._parse(response, len(texts))
                error = EmbeddingError(f"HTTP {response.status_code}: {response.text[:300]}")
                retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable or attempt >= self._max_retries:
                logger.error(
                    "embedding request failed",
                    extra={
                        "fields": {
                            "error": repr(error),
                            "attempts": attempt + 1,
                            "elapsed_ms": int((time.perf_counter() - started) * 1000),
                        }
                    },
                )
                raise EmbeddingError(str(error)) from error
            delay = self._backoff * (2**attempt) + random.uniform(0, self._backoff)
            attempt += 1
            await asyncio.sleep(delay)

    def _parse(self, response: httpx.Response, expected: int) -> list[list[float]]:
        try:
            data = response.json()["data"]
            ordered = sorted(data, key=lambda d: int(d["index"]))
            vectors = [[float(x) for x in item["embedding"]] for item in ordered]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError(f"invalid embeddings response: {exc!r}") from exc
        if len(vectors) != expected or any(len(v) != self._dimensions for v in vectors):
            raise EmbeddingError("embeddings response has unexpected shape")
        return vectors


def create_embedding_client(settings: Settings, http: httpx.AsyncClient) -> EmbeddingClient:
    if settings.embedding_mode == "live":
        return OpenAICompatibleEmbedding(settings, http)
    return HashEmbedding(settings.embedding_dimensions)
