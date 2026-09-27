"""`X-API-Key` authentication. Every endpoint except `GET /api/health` depends on `require_api_key`.

The browser cannot attach a header to `<img src>` requests, so the same key is also accepted from the cookie
`psk` (set by the web UI, SameSite=Strict) — but ONLY on the GET routes that serve image files
(`require_api_key_or_cookie`). Every other route is header-only, so a same-site page on another 127.0.0.1 port
(SameSite ignores the port) cannot trigger state changes with the cookie. This app is local-only; see README.
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


def _check(request: Request, provided: str | None) -> str:
    expected: str = request.app.state.settings.api_key.get_secret_value()
    if not provided or not secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=AUTH_ERROR_MESSAGE)
    return api_key_id(provided)


async def require_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias=HEADER_NAME)] = None,
) -> str:
    """Header authentication (all API routes)."""
    return _check(request, x_api_key)


async def require_api_key_or_cookie(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias=HEADER_NAME)] = None,
    psk: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> str:
    """Header or cookie authentication — GET file routes only (`<img src>` cannot send headers)."""
    if request.method != "GET":
        return _check(request, x_api_key)
    return _check(request, x_api_key or psk)


ApiKeyDep = Annotated[str, Depends(require_api_key)]
