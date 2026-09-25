"""Supabase JWT の検証（`Authorization: Bearer <access_token>`）。

- 非対称鍵（ES256 / RS256）: `{SUPABASE_URL}/auth/v1/.well-known/jwks.json` から公開鍵を取得。
  TTL 付きでキャッシュし、未知の `kid` を受け取ったら（クールダウン付きで）再取得する。
  取得に失敗して検証に使える鍵が無い間は、トークンの正否を判定できないので 401 ではなく
  503（internal_error）を返す（Web は 401 でログアウトさせるため、認証サーバーの障害で全員をログアウトさせない）。
  失敗直後は `failure_cooldown_seconds` の間だけ再取得せずに即座に失敗させる（待ち行列で1件ずつ 5 秒待たせない）。
- 共有鍵（HS256・旧方式）: `SUPABASE_JWT_SECRET` が設定されている場合のみ受け付ける。
- 検証項目: 署名 / exp / aud / sub（UUID）/ iss（トークンに含まれる場合）/ role=authenticated。
- 認証失敗（401）は DB を汚さないよう stdout にのみ `auth.failure` を記録する。
  トークンは正しいが退会済み・プロフィール無し（403）の場合は audit_logs にも記録する。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Final
from uuid import UUID

import httpx
import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings
from app.core.errors import AUTH_UNAVAILABLE_MESSAGE, ApiError
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.container import Services

logger = get_logger("auth")

ASYMMETRIC_ALGORITHMS: Final[frozenset[str]] = frozenset({"ES256", "RS256"})
CLOCK_SKEW_LEEWAY_SECONDS: Final[int] = 30


class AuthError(Exception):
    """トークン検証の失敗。`reason` はログ用の機械可読な理由。"""

    def __init__(self, reason: str, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(reason if detail is None else f"{reason}: {detail}")


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: UUID
    email: str | None


@dataclass(frozen=True, slots=True)
class VerifiedToken:
    user_id: UUID
    email: str | None
    claims: dict[str, Any]


class JwksCache:
    """Supabase Auth の JWKS を取得・キャッシュする。"""

    def __init__(
        self,
        url: str,
        http: httpx.AsyncClient,
        *,
        ttl_seconds: float = 600.0,
        min_refetch_interval_seconds: float = 30.0,
        failure_cooldown_seconds: float = 5.0,
        timeout_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._http = http
        self._ttl = ttl_seconds
        self._min_refetch = min_refetch_interval_seconds
        self._failure_cooldown = failure_cooldown_seconds
        self._timeout = timeout_seconds
        self._clock = clock
        self._keys: list[tuple[str | None, jwt.PyJWK]] = []
        self._fetched_at: float | None = None
        # 直近の取得が失敗した時刻（成功したら None に戻す）
        self._failed_at: float | None = None
        self._lock = asyncio.Lock()

    async def warm_up(self) -> None:
        """起動時に JWKS を先に取得する（失敗しても例外にしない。ログは _refresh が出す）。"""
        try:
            await self._refresh(force=False)
        except AuthError as exc:
            logger.warning("JWKS warm-up failed; will retry on demand", extra={"fields": {"reason": exc.reason}})

    async def get_key(self, kid: str | None, alg: str) -> jwt.PyJWK:
        if self._is_stale():
            await self._refresh(force=False)
        key = self._find(kid, alg)
        if key is None and self._can_refetch():
            # 鍵ローテーション直後など未知の kid → 再取得（クールダウン付き）
            await self._refresh(force=True)
            key = self._find(kid, alg)
        if key is None:
            if self._failed_at is not None:
                # 最新の鍵を取得できていない（ローテーション直後の障害など）。トークンが不正とは言い切れない
                raise AuthError("jwks_unavailable", f"kid={kid} alg={alg}; last JWKS fetch failed")
            raise AuthError("unknown_signing_key", f"kid={kid} alg={alg}")
        return key

    def _is_stale(self) -> bool:
        return self._fetched_at is None or self._clock() - self._fetched_at > self._ttl

    def _can_refetch(self) -> bool:
        return self._fetched_at is None or self._clock() - self._fetched_at >= self._min_refetch

    def _recently_failed(self) -> bool:
        """直前の取得が失敗してから failure_cooldown_seconds 以内か（この間は再取得しない）。"""
        return self._failed_at is not None and self._clock() - self._failed_at < self._failure_cooldown

    def _skip_after_failure(self, *, force: bool) -> bool:
        """失敗直後の再取得を省くか。

        鍵が1つも無ければ AuthError（jwks_unavailable）。鍵があり、未知の kid による強制再取得なら True
        （呼び出し側の get_key が「直前の取得が失敗している」として jwks_unavailable にする）。
        """
        if not self._recently_failed():
            return False
        if not self._keys:
            raise AuthError("jwks_unavailable", "cooling down after a failed fetch")
        return force

    def _find(self, kid: str | None, alg: str) -> jwt.PyJWK | None:
        candidates = [k for k_id, k in self._keys if k.algorithm_name == alg]
        if kid is not None:
            for k_id, k in self._keys:
                if k_id == kid and k.algorithm_name == alg:
                    return k
            return None
        return candidates[0] if len(candidates) == 1 else None

    async def _refresh(self, *, force: bool) -> None:
        # 失敗直後はロック待ちの行列に並ばせず、すぐに失敗させる
        if self._skip_after_failure(force=force):
            return
        async with self._lock:
            # ロック待ちの間に別コルーチンが取得済みなら何もしない
            if not force and not self._is_stale():
                return
            if force and not self._can_refetch():
                return
            # ロック待ちの間に別コルーチンの取得が失敗した → 同じ取得を繰り返さない
            if self._skip_after_failure(force=force):
                return
            try:
                response = await self._http.get(self._url, timeout=self._timeout)
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                self._failed_at = self._clock()
                logger.error(
                    "failed to fetch JWKS",
                    extra={"fields": {"url": self._url, "error": repr(exc), "cached_keys": len(self._keys)}},
                )
                if self._keys:
                    # 取得失敗時は既存キャッシュを使い続ける（次回はクールダウン後に再試行）
                    self._fetched_at = self._clock() - self._ttl + self._min_refetch
                    return
                raise AuthError("jwks_unavailable", repr(exc)) from exc
            self._keys = self._parse(data)
            self._fetched_at = self._clock()
            self._failed_at = None
            logger.info("JWKS refreshed", extra={"fields": {"url": self._url, "keys": len(self._keys)}})

    @staticmethod
    def _parse(data: object) -> list[tuple[str | None, jwt.PyJWK]]:
        keys: list[tuple[str | None, jwt.PyJWK]] = []
        raw_keys = data.get("keys") if isinstance(data, dict) else None
        if not isinstance(raw_keys, list):
            return keys
        for raw in raw_keys:
            if not isinstance(raw, dict) or raw.get("use", "sig") != "sig":
                continue
            try:
                key = jwt.PyJWK(raw)
            except (jwt.PyJWKError, jwt.InvalidKeyError) as exc:
                logger.warning("skipping unusable JWK", extra={"fields": {"kid": raw.get("kid"), "error": repr(exc)}})
                continue
            if key.algorithm_name not in ASYMMETRIC_ALGORITHMS:
                continue
            kid = raw.get("kid")
            keys.append((kid if isinstance(kid, str) else None, key))
        return keys


class TokenVerifier:
    def __init__(self, settings: Settings, jwks: JwksCache) -> None:
        self._audience = settings.supabase_jwt_audience
        self._issuer = settings.jwt_issuer
        secret = settings.supabase_jwt_secret
        self._hs_secret = secret.get_secret_value() if secret and secret.get_secret_value() else None
        self._jwks = jwks

    async def verify(self, token: str) -> VerifiedToken:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise AuthError("malformed_token", repr(exc)) from exc
        alg = header.get("alg")
        kid = header.get("kid")
        key: Any
        if alg in ASYMMETRIC_ALGORITHMS:
            key = (await self._jwks.get_key(kid if isinstance(kid, str) else None, str(alg))).key
        elif alg == "HS256":
            if self._hs_secret is None:
                raise AuthError("hs256_not_configured")
            key = self._hs_secret
        else:
            raise AuthError("unsupported_algorithm", str(alg))

        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=[str(alg)],
                audience=self._audience,
                leeway=CLOCK_SKEW_LEEWAY_SECONDS,
                options={"require": ["exp", "sub", "aud"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("token_expired") from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthError("invalid_audience") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthError("invalid_token", repr(exc)) from exc

        iss = claims.get("iss")
        if iss is not None and str(iss).rstrip("/") != self._issuer:
            raise AuthError("invalid_issuer", str(iss))
        role = claims.get("role")
        if role is not None and role != "authenticated":
            raise AuthError("invalid_role", str(role))
        if claims.get("is_anonymous") is True:
            raise AuthError("anonymous_not_allowed")
        try:
            user_id = UUID(str(claims["sub"]))
        except ValueError as exc:
            raise AuthError("invalid_subject") from exc
        email = claims.get("email")
        return VerifiedToken(user_id=user_id, email=email if isinstance(email, str) else None, claims=claims)


bearer_scheme = HTTPBearer(auto_error=False, description="Supabase Auth のアクセストークン（JWT）")


def _services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> CurrentUser:
    """検証済みユーザーを返す FastAPI 依存関係。退会済みは 403 account_deleted。"""
    services = _services(request)
    if credentials is None:
        _log_auth_failure(request, "missing_token")
        raise ApiError(401, "unauthorized", "ログインが必要です。")
    try:
        verified = await services.token_verifier.verify(credentials.credentials)
    except AuthError as exc:
        _log_auth_failure(request, exc.reason, exc.detail)
        if exc.reason == "jwks_unavailable":
            # 認証サーバー側の障害。401 にするとクライアントがログアウトしてしまうため 503（再試行可能）
            raise ApiError(503, "internal_error", AUTH_UNAVAILABLE_MESSAGE, headers={"Retry-After": "5"}) from exc
        raise ApiError(401, "unauthorized") from exc

    row = await services.pool.fetchrow("select deleted_at from public.profiles where id = $1", verified.user_id)
    if row is None:
        await services.audit.log(
            "auth.failure",
            user_id=verified.user_id,
            payload={"reason": "profile_missing", "path": request.url.path},
        )
        raise ApiError(403, "forbidden", "アカウント情報が見つかりません。もう一度ログインしてください。")
    if row["deleted_at"] is not None:
        await services.audit.log(
            "auth.failure",
            user_id=verified.user_id,
            payload={"reason": "account_deleted", "path": request.url.path},
        )
        raise ApiError(403, "account_deleted")

    request.state.user_id = str(verified.user_id)
    return CurrentUser(id=verified.user_id, email=verified.email)


def _log_auth_failure(request: Request, reason: str, detail: str | None = None) -> None:
    logger.warning(
        "auth.failure",
        extra={
            "fields": {
                "event_type": "auth.failure",
                "reason": reason,
                "detail": detail,
                "path": request.url.path,
                # RequestContextMiddleware が信頼済みヘッダーから解決した値（偽装不可）
                "client_ip": getattr(request.state, "client_ip", None)
                or (request.client.host if request.client else None),
            }
        },
    )


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
