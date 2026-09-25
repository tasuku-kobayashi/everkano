"""サービス層で共有する値オブジェクト。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

SenderType = Literal["user", "character"]

MEMORY_TAG_SECRET = "secret"  # noqa: S105 - 「二人だけの秘密」タグ（パスワードではない）
MEMORY_TAG_SUMMARY = "summary"  # 中期メモリ（自動要約）


@dataclass(frozen=True, slots=True)
class HistoryItem:
    """短期メモリ（messages テーブルの1行）。"""

    id: UUID
    sender_type: SenderType
    body: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RetrievedMemory:
    """長期メモリの検索結果。similarity はコサイン類似度（要約の常時注入分は None の場合あり）。"""

    id: UUID
    content: str
    importance: float
    tags: tuple[str, ...]
    similarity: float | None
    created_at: datetime

    @property
    def is_secret(self) -> bool:
        return MEMORY_TAG_SECRET in self.tags

    @property
    def is_summary(self) -> bool:
        return MEMORY_TAG_SUMMARY in self.tags


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    """会話から抽出された記憶候補（§9.2）。"""

    content: str
    importance: float
    category: str | None = None


@dataclass(frozen=True, slots=True)
class CharacterRecord:
    """characters テーブルのサーバー専用列を含む行。"""

    id: UUID
    handle: str
    name: str
    avatar_url: str
    bio: str | None
    persona_key: str
    system_prompt: str
    is_active: bool
