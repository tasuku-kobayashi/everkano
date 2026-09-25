from __future__ import annotations

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
