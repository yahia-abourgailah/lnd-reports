"""The OIDC flow, exercised against a stubbed provider.

Signature verification is the whole security of the login, so it is tested with
a real key pair rather than a mock: a token signed by the right key is accepted,
and one signed by another key, or carrying the wrong nonce, issuer or audience,
is not.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from authlib.jose import JsonWebKey, JsonWebToken

from lnd.auth import oidc

ISSUER = "https://idp.example.com"
CLIENT_ID = "lnd-analytics"


@pytest.fixture
def keypair() -> Any:
    return JsonWebKey.generate_key("RSA", 2048, is_private=True)


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch, keypair: Any) -> Any:
    meta = oidc.ProviderMetadata(
        issuer=ISSUER,
        authorization_endpoint=f"{ISSUER}/authorize",
        token_endpoint=f"{ISSUER}/token",
        jwks_uri=f"{ISSUER}/jwks",
        end_session_endpoint=None,
        token_endpoint_auth_methods=("client_secret_post",),
    )

    async def fake_metadata(*, force: bool = False) -> oidc.ProviderMetadata:
        return meta

    async def fake_jwks(*, force: bool = False) -> Any:
        return JsonWebKey.import_key_set({"keys": [keypair.as_dict(is_private=False)]})

    monkeypatch.setattr(oidc, "get_metadata", fake_metadata)
    monkeypatch.setattr(oidc, "get_jwks", fake_jwks)
    monkeypatch.setenv("OIDC_CLIENT_ID", CLIENT_ID)

    from lnd.config import get_settings

    get_settings.cache_clear()
    return meta


def _id_token(key: Any, **overrides: Any) -> str:
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "0000-1111",
        "email": "Specialist@Example.com",
        "name": "L&D Specialist",
        "nonce": "the-nonce",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    claims.update(overrides)
    header = {"alg": "RS256", "kid": key.thumbprint()}
    return JsonWebToken(["RS256"]).encode(header, claims, key).decode("ascii")


# ---------------------------------------------------------------- happy path
@pytest.mark.anyio
async def test_a_correctly_signed_token_is_accepted(provider: Any, keypair: Any) -> None:
    claims = await oidc.verify_id_token(_id_token(keypair), "the-nonce")
    assert claims["sub"] == "0000-1111"


def test_claims_map_onto_a_principal() -> None:
    principal = oidc.principal_from_claims(
        {"sub": "abc", "email": "Specialist@Example.com", "name": "L&D Specialist"}
    )
    assert principal.email == "specialist@example.com"  # normalised on the way in
    assert principal.name == "L&D Specialist"
    assert principal.subject == "abc"


def test_entra_style_preferred_username_is_used_when_email_is_absent() -> None:
    principal = oidc.principal_from_claims(
        {"sub": "abc", "preferred_username": "specialist@example.com"}
    )
    assert principal.email == "specialist@example.com"
    assert principal.name == "specialist"


def test_a_token_with_no_address_is_refused() -> None:
    with pytest.raises(oidc.OidcError, match="no email claim"):
        oidc.principal_from_claims({"sub": "abc"})


# ------------------------------------------------------------------ refusals
@pytest.mark.anyio
async def test_a_token_signed_by_another_key_is_refused(provider: Any) -> None:
    attacker = JsonWebKey.generate_key("RSA", 2048, is_private=True)
    with pytest.raises(oidc.OidcError, match="rejected"):
        await oidc.verify_id_token(_id_token(attacker), "the-nonce")


@pytest.mark.anyio
async def test_a_replayed_nonce_is_refused(provider: Any, keypair: Any) -> None:
    with pytest.raises(oidc.OidcError, match="rejected"):
        await oidc.verify_id_token(_id_token(keypair), "a-different-nonce")


@pytest.mark.anyio
async def test_a_token_from_another_issuer_is_refused(provider: Any, keypair: Any) -> None:
    with pytest.raises(oidc.OidcError, match="rejected"):
        await oidc.verify_id_token(_id_token(keypair, iss="https://evil.example.com"), "the-nonce")


@pytest.mark.anyio
async def test_a_token_for_another_audience_is_refused(provider: Any, keypair: Any) -> None:
    with pytest.raises(oidc.OidcError, match="rejected"):
        await oidc.verify_id_token(_id_token(keypair, aud="some-other-app"), "the-nonce")


@pytest.mark.anyio
async def test_an_expired_token_is_refused(provider: Any, keypair: Any) -> None:
    expired = _id_token(keypair, exp=int(time.time()) - 3600, iat=int(time.time()) - 7200)
    with pytest.raises(oidc.OidcError, match="rejected"):
        await oidc.verify_id_token(expired, "the-nonce")


# ---------------------------------------------------------------------- PKCE
def test_pkce_challenge_is_s256_of_the_verifier() -> None:
    import base64
    import hashlib

    verifier, challenge = oidc._pkce_pair()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    assert challenge == expected
    assert "=" not in challenge


@pytest.mark.anyio
async def test_the_authorization_url_carries_state_nonce_and_pkce(provider: Any) -> None:
    from urllib.parse import parse_qs, urlparse

    url, tx = await oidc.build_authorization_url()
    params = parse_qs(urlparse(url).query)

    assert params["response_type"] == ["code"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["state"] == [tx["state"]]
    assert params["nonce"] == [tx["nonce"]]
    assert tx["verifier"] not in url  # the verifier never leaves us


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
