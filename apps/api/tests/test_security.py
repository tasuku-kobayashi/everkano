from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from app.core.security import AuthError, JwksCache, TokenVerifier
from tests.conftest import TEST_ISSUER, TEST_JWT_SECRET, make_settings, make_token

JWKS_URL = "http://127.0.0.1:54321/auth/v1/.well-known/jwks.json"


def _jwk(public_key: Any, kid: str, alg: str) -> dict[str, Any]:
    algo = ECAlgorithm if alg == "ES256" else RSAAlgorithm
    data: dict[str, Any] = json.loads(algo.to_jwk(public_key))
    data.update({"kid": kid, "alg": alg, "use": "sig"})
    return data


class FakeJwks:
    """JWKS エンドポイントのモック。keys を差し替えるとローテーションを再現できる。"""

    def __init__(self, keys: list[dict[str, Any]]) -> None:
        self.keys = keys
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == JWKS_URL
        self.calls += 1
        return httpx.Response(200, json={"keys": self.keys})


def _claims(user_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims = {
        "sub": str(user_id),
        "aud": "authenticated",
        "role": "authenticated",
        "iss": TEST_ISSUER,
        "email": "a@example.test",
        "iat": now,
        "exp": now + 600,
    }
    claims.update(overrides)
    return claims


def _verifier(jwks: FakeJwks | None = None, **settings: Any) -> TokenVerifier:
    transport = httpx.MockTransport(jwks.handler if jwks else lambda _: httpx.Response(500))
    http = httpx.AsyncClient(transport=transport)
    s = make_settings(**settings)
    return TokenVerifier(s, JwksCache(s.jwks_url, http, ttl_seconds=600, min_refetch_interval_seconds=0))


# --------------------------------------------------------------------------- HS256


async def test_hs256_valid() -> None:
    user_id = uuid.uuid4()
    verified = await _verifier().verify(make_token(user_id, email="x@example.test"))
    assert verified.user_id == user_id
    assert verified.email == "x@example.test"


async def test_hs256_expired() -> None:
    token = make_token(uuid.uuid4(), expires_in=-3600)
    with pytest.raises(AuthError) as exc:
        await _verifier().verify(token)
    assert exc.value.reason == "token_expired"


async def test_hs256_wrong_audience() -> None:
    token = make_token(uuid.uuid4(), audience="anon")
    with pytest.raises(AuthError) as exc:
        await _verifier().verify(token)
    assert exc.value.reason == "invalid_audience"


async def test_hs256_wrong_secret() -> None:
    token = make_token(uuid.uuid4(), secret="another-secret-another-secret-0123456789")
    with pytest.raises(AuthError) as exc:
        await _verifier().verify(token)
    assert exc.value.reason == "invalid_token"


async def test_hs256_rejected_when_secret_not_configured() -> None:
    with pytest.raises(AuthError) as exc:
        await _verifier(supabase_jwt_secret=None).verify(make_token(uuid.uuid4()))
    assert exc.value.reason == "hs256_not_configured"


async def test_wrong_issuer_rejected_and_missing_issuer_accepted() -> None:
    with pytest.raises(AuthError) as exc:
        await _verifier().verify(make_token(uuid.uuid4(), issuer="https://evil.example/auth/v1"))
    assert exc.value.reason == "invalid_issuer"
    verified = await _verifier().verify(make_token(uuid.uuid4(), issuer=None))
    assert verified.user_id


async def test_non_authenticated_role_and_bad_subject_rejected() -> None:
    with pytest.raises(AuthError):
        await _verifier().verify(make_token(uuid.uuid4(), extra={"role": "service_role"}))
    with pytest.raises(AuthError):
        await _verifier().verify(make_token("not-a-uuid"))
    with pytest.raises(AuthError):
        await _verifier().verify(make_token(uuid.uuid4(), extra={"is_anonymous": True}))


async def test_alg_none_and_garbage_rejected() -> None:
    unsigned = jwt.encode(_claims(uuid.uuid4()), key=None, algorithm="none")
    with pytest.raises(AuthError) as exc:
        await _verifier().verify(unsigned)
    assert exc.value.reason == "unsupported_algorithm"
    with pytest.raises(AuthError) as exc:
        await _verifier().verify("garbage")
    assert exc.value.reason == "malformed_token"


# --------------------------------------------------------------------------- ES256 / RS256 (JWKS)


async def test_es256_via_jwks() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    jwks = FakeJwks([_jwk(key.public_key(), "kid-1", "ES256")])
    verifier = _verifier(jwks, supabase_jwt_secret=None)
    user_id = uuid.uuid4()
    token = jwt.encode(_claims(user_id), key, algorithm="ES256", headers={"kid": "kid-1"})
    verified = await verifier.verify(token)
    assert verified.user_id == user_id
    # キャッシュされる
    await verifier.verify(token)
    assert jwks.calls == 1


async def test_rs256_via_jwks() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = FakeJwks([_jwk(key.public_key(), "rsa-1", "RS256")])
    token = jwt.encode(_claims(uuid.uuid4()), key, algorithm="RS256", headers={"kid": "rsa-1"})
    assert (await _verifier(jwks).verify(token)).user_id


async def test_es256_unknown_kid_triggers_refetch() -> None:
    old_key = ec.generate_private_key(ec.SECP256R1())
    new_key = ec.generate_private_key(ec.SECP256R1())
    jwks = FakeJwks([_jwk(old_key.public_key(), "old", "ES256")])
    verifier = _verifier(jwks)
    await verifier.verify(jwt.encode(_claims(uuid.uuid4()), old_key, algorithm="ES256", headers={"kid": "old"}))
    assert jwks.calls == 1
    # 鍵ローテーション
    jwks.keys = [_jwk(new_key.public_key(), "new", "ES256")]
    token = jwt.encode(_claims(uuid.uuid4()), new_key, algorithm="ES256", headers={"kid": "new"})
    assert (await verifier.verify(token)).user_id
    assert jwks.calls == 2


async def test_es256_signed_by_unknown_key_is_rejected() -> None:
    trusted = ec.generate_private_key(ec.SECP256R1())
    attacker = ec.generate_private_key(ec.SECP256R1())
    jwks = FakeJwks([_jwk(trusted.public_key(), "kid-1", "ES256")])
    token = jwt.encode(_claims(uuid.uuid4()), attacker, algorithm="ES256", headers={"kid": "kid-1"})
    with pytest.raises(AuthError) as exc:
        await _verifier(jwks).verify(token)
    assert exc.value.reason == "invalid_token"
    unknown_kid = jwt.encode(_claims(uuid.uuid4()), attacker, algorithm="ES256", headers={"kid": "nope"})
    with pytest.raises(AuthError) as exc:
        await _verifier(jwks).verify(unknown_kid)
    assert exc.value.reason == "unknown_signing_key"


async def test_es256_expired_and_wrong_audience() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    jwks = FakeJwks([_jwk(key.public_key(), "k", "ES256")])
    expired = jwt.encode(
        _claims(uuid.uuid4(), exp=int(time.time()) - 3600), key, algorithm="ES256", headers={"kid": "k"}
    )
    with pytest.raises(AuthError, match="token_expired"):
        await _verifier(jwks).verify(expired)
    wrong_aud = jwt.encode(_claims(uuid.uuid4(), aud="other"), key, algorithm="ES256", headers={"kid": "k"})
    with pytest.raises(AuthError, match="invalid_audience"):
        await _verifier(jwks).verify(wrong_aud)


async def test_jwks_unavailable() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    token = jwt.encode(_claims(uuid.uuid4()), key, algorithm="ES256", headers={"kid": "k"})
    with pytest.raises(AuthError) as exc:
        await _verifier(None).verify(token)
    assert exc.value.reason == "jwks_unavailable"


async def test_hs256_token_cannot_be_forged_with_jwks_public_key() -> None:
    """公開鍵を HS256 の共有鍵として使うアルゴリズム混同攻撃を拒否する。"""
    key = ec.generate_private_key(ec.SECP256R1())
    jwks = FakeJwks([_jwk(key.public_key(), "k", "ES256")])
    token = jwt.encode(_claims(uuid.uuid4()), "not-the-secret-not-the-secret-0123456789", algorithm="HS256")
    with pytest.raises(AuthError):
        await _verifier(jwks, supabase_jwt_secret=TEST_JWT_SECRET).verify(token)


# --------------------------------------------------------------------------- JWKS 障害時の振る舞い


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FlakyJwks:
    """JWKS エンドポイントのモック（失敗させる・遅延させることができる）。"""

    def __init__(self, keys: list[dict[str, Any]] | None = None, *, fail: bool = True, delay: float = 0.0) -> None:
        self.keys = keys or []
        self.fail = fail
        self.delay = delay
        self.calls = 0

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            return httpx.Response(503)
        return httpx.Response(200, json={"keys": self.keys})


def _cache(jwks: FlakyJwks, clock: FakeClock) -> JwksCache:
    http = httpx.AsyncClient(transport=httpx.MockTransport(jwks.handler))
    return JwksCache(JWKS_URL, http, ttl_seconds=600, min_refetch_interval_seconds=30, clock=clock)


async def test_jwks_cold_start_failure_fetches_once_and_fails_fast() -> None:
    """鍵が無い状態で取得に失敗したら、待ち行列の各リクエストが順番に再取得しない（1回だけ）。"""
    jwks = FlakyJwks(fail=True, delay=0.05)
    clock = FakeClock()
    cache = _cache(jwks, clock)

    results = await asyncio.gather(*(cache.get_key("k", "ES256") for _ in range(10)), return_exceptions=True)
    assert all(isinstance(r, AuthError) and r.reason == "jwks_unavailable" for r in results)
    assert jwks.calls == 1

    # クールダウン中は取得しない
    with pytest.raises(AuthError, match="jwks_unavailable"):
        await cache.get_key("k", "ES256")
    assert jwks.calls == 1

    # クールダウン後は再取得し、復旧していれば成功する
    key = ec.generate_private_key(ec.SECP256R1())
    jwks.fail = False
    jwks.keys = [_jwk(key.public_key(), "k", "ES256")]
    clock.now += 6
    assert (await cache.get_key("k", "ES256")) is not None
    assert jwks.calls == 2


async def test_unknown_kid_while_jwks_is_down_is_unavailable_not_invalid() -> None:
    """鍵ローテーション直後に JWKS が落ちている場合、新しい kid のトークンを「不正」（401）にしない。"""
    old = ec.generate_private_key(ec.SECP256R1())
    jwks = FlakyJwks([_jwk(old.public_key(), "old", "ES256")], fail=False)
    clock = FakeClock()
    cache = _cache(jwks, clock)
    assert await cache.get_key("old", "ES256")

    jwks.fail = True
    clock.now += 31  # 強制再取得のクールダウン（30 秒）後
    with pytest.raises(AuthError) as exc:
        await cache.get_key("new", "ES256")
    assert exc.value.reason == "jwks_unavailable"
    # 既知の鍵のトークンは、取得失敗中もキャッシュで検証できる
    assert await cache.get_key("old", "ES256")

    # 復旧後、本当に存在しない kid は unknown_signing_key（401）
    jwks.fail = False
    clock.now += 31
    with pytest.raises(AuthError) as exc:
        await cache.get_key("nope", "ES256")
    assert exc.value.reason == "unknown_signing_key"


async def test_warm_up_never_raises() -> None:
    clock = FakeClock()
    down = FlakyJwks(fail=True)
    await _cache(down, clock).warm_up()
    assert down.calls == 1
    key = ec.generate_private_key(ec.SECP256R1())
    up = FlakyJwks([_jwk(key.public_key(), "k", "ES256")], fail=False)
    cache = _cache(up, clock)
    await cache.warm_up()
    assert await cache.get_key("k", "ES256")
    assert up.calls == 1


async def test_stale_cache_keeps_serving_known_keys_when_refresh_fails() -> None:
    """TTL 切れの再取得が失敗しても、キャッシュ済みの鍵で検証を続ける（認証サーバーの障害で全員を 401 にしない）。"""
    key = ec.generate_private_key(ec.SECP256R1())
    jwks = FlakyJwks([_jwk(key.public_key(), "k", "ES256")], fail=False)
    clock = FakeClock()
    cache = _cache(jwks, clock)
    assert await cache.get_key("k", "ES256")
    assert jwks.calls == 1

    jwks.fail = True
    clock.now += 601  # TTL（600 秒）切れ
    assert await cache.get_key("k", "ES256")
    assert jwks.calls == 2
    # 失敗後は min_refetch（30 秒）の間は再取得しない
    clock.now += 10
    assert await cache.get_key("k", "ES256")
    assert jwks.calls == 2
    clock.now += 21
    assert await cache.get_key("k", "ES256")
    assert jwks.calls == 3
    # 復旧すれば通常の TTL に戻る
    jwks.fail = False
    clock.now += 31
    assert await cache.get_key("k", "ES256")
    assert jwks.calls == 4
    clock.now += 100
    assert await cache.get_key("k", "ES256")
    assert jwks.calls == 4


async def test_unknown_kid_right_after_a_failed_refetch_does_not_refetch_again() -> None:
    """失敗直後（failure_cooldown_seconds 以内）の未知の kid は、再取得せずに jwks_unavailable（503）。"""
    key = ec.generate_private_key(ec.SECP256R1())
    jwks = FlakyJwks([_jwk(key.public_key(), "k", "ES256")], fail=False)
    clock = FakeClock()
    cache = _cache(jwks, clock)
    assert await cache.get_key("k", "ES256")
    jwks.fail = True
    clock.now += 31
    for kid in ("new-1", "new-2", "new-3"):
        with pytest.raises(AuthError) as exc:
            await cache.get_key(kid, "ES256")
        assert exc.value.reason == "jwks_unavailable"
    assert jwks.calls == 2  # 最初の取得 + 失敗した1回だけ


async def test_concurrent_cold_start_fetches_jwks_once() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    jwks = FlakyJwks([_jwk(key.public_key(), "k", "ES256")], fail=False, delay=0.05)
    cache = _cache(jwks, FakeClock())
    results = await asyncio.gather(*(cache.get_key("k", "ES256") for _ in range(10)))
    assert all(r is not None for r in results)
    assert jwks.calls == 1


@pytest.mark.parametrize("fail", [False, True])
async def test_concurrent_unknown_kid_refetches_once(fail: bool) -> None:
    """鍵ローテーション直後に新しい kid のトークンが同時に届いても、再取得は1回だけ（成功・失敗とも）。"""
    old = ec.generate_private_key(ec.SECP256R1())
    new = ec.generate_private_key(ec.SECP256R1())
    jwks = FlakyJwks([_jwk(old.public_key(), "old", "ES256")], fail=False, delay=0.05)
    clock = FakeClock()
    cache = _cache(jwks, clock)
    assert await cache.get_key("old", "ES256")
    jwks.keys = [_jwk(old.public_key(), "old", "ES256"), _jwk(new.public_key(), "new", "ES256")]
    jwks.fail = fail
    clock.now += 31
    results = await asyncio.gather(*(cache.get_key("new", "ES256") for _ in range(5)), return_exceptions=True)
    if fail:
        assert all(isinstance(r, AuthError) and r.reason == "jwks_unavailable" for r in results)
    else:
        assert not any(isinstance(r, BaseException) for r in results)
    assert jwks.calls == 2


async def test_unusable_and_symmetric_jwks_entries_are_skipped() -> None:
    good = ec.generate_private_key(ec.SECP256R1())
    second = ec.generate_private_key(ec.SECP256R1())
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    entries: list[Any] = [
        "not-a-dict",
        {**_jwk(good.public_key(), "enc", "ES256"), "use": "enc"},  # 署名用でない
        {"kty": "EC", "crv": "P-256", "kid": "broken", "x": "AAAA", "y": "AAAA"},  # 壊れた鍵
        {"kty": "oct", "k": "c2VjcmV0", "kid": "hs", "alg": "HS256"},  # 共有鍵は JWKS から受け付けない
        _jwk(good.public_key(), "good", "ES256"),
        _jwk(second.public_key(), "second", "ES256"),
        {**_jwk(rsa_key.public_key(), "rsa", "RS256"), "kid": 123},  # kid が文字列でない → kid 無し扱い
    ]
    jwks = FlakyJwks(entries, fail=False)
    cache = _cache(jwks, FakeClock())
    assert await cache.get_key("good", "ES256")
    for kid in ("enc", "broken", "hs"):
        with pytest.raises(AuthError, match="unknown_signing_key"):
            await cache.get_key(kid, "ES256" if kid != "hs" else "HS256")
    # kid の無いトークンは、その alg の鍵が1つだけなら使う（複数あれば曖昧なので使わない）
    assert await cache.get_key(None, "RS256")
    with pytest.raises(AuthError, match="unknown_signing_key"):
        await cache.get_key(None, "ES256")
    assert jwks.calls == 1


@pytest.mark.parametrize("body", [{"keys": "not-a-list"}, ["not", "a", "dict"], {"no_keys": []}])
async def test_malformed_jwks_document_yields_no_keys(body: Any) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    cache = JwksCache(JWKS_URL, httpx.AsyncClient(transport=httpx.MockTransport(handler)), clock=FakeClock())
    with pytest.raises(AuthError, match="unknown_signing_key"):
        await cache.get_key("k", "ES256")
