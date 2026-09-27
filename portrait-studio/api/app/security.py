"""`X-API-Key` authentication. Every endpoint except `GET /api/health` depends on `require_api_key`.

The browser cannot attach a header to `<img src>` requests, so the same key is also accepted from the cookie
`psk` (set by the web UI, SameSite=Strict). This app is local-only; see README before exposing it anywhere.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status

HEADER_NAME = "X-API-Key"
COOKIE_NAME = "psk"
AUTH_ERROR_MESSAGE = "APIキーが無効です。ヘッダ X-API-Key を確認してください。"


def api_key_id(key: str) -> str:
    """Short, non-reversible identifier of a key for the audit log (never log the key itself)."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


async def require_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias=HEADER_NAME)] = None,
    psk: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> str:
    expected: str = request.app.state.settings.api_key.get_secret_value()
    provided = x_api_key or psk
    if not provided or not secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=AUTH_ERROR_MESSAGE)
    return api_key_id(provided)


ApiKeyDep = Annotated[str, Depends(require_api_key)]
