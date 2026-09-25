from __future__ import annotations

from typing import Literal

from app.models.common import ApiModel


class HealthResponse(ApiModel):
    status: Literal["ok", "degraded"]
    version: str
    env: str
    llm_mode: Literal["live", "mock"]
    embedding_mode: Literal["live", "hash"]
    db: Literal["ok", "error"]
