"""OIDC authorization-code flow with PKCE.

Written against the discovery document rather than any one provider, so pointing
OIDC_DISCOVERY_URL at Entra ID, Okta or Keycloak is a configuration change and
not a code change.

The ID token is verified — signature against the provider's JWKS, plus issuer,
audience, expiry and nonce. An unverified ID token is a login bypass, so this is
the one place in the codebase where nothing is taken on trust.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlencode

import httpx
from authlib.jose import JsonWebKey, JsonWebToken
from authlib.jose.errors import JoseError

from lnd.auth.principal import Principal
from lnd.config import get_settings

log = logging.getLogger(__name__)

_DISCOVERY_TTL = 3600.0
_JWKS_TTL = 3600.0
_HTTP_TIMEOUT = 10.0

# Providers must sign with an asymmetric algorithm. Listing them explicitly
# stops a token that claims alg=none or a symmetric alg from being accepted.
_ALLOWED_ALGS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]


class OidcError(RuntimeError):
    """Any failure in the login flow. The message is logged, never shown."""


@dataclass(frozen=True)
class ProviderMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    end_session_endpoint: str | None
    token_endpoint_auth_methods: tuple[str, ...]


_metadata: tuple[ProviderMetadata, float] | None = None
_jwks: tuple[Any, float] | None = None


async def get_metadata(*, force: bool = False) -> ProviderMetadata:
    global _metadata
    now = time.monotonic()
    if not force and _metadata is not None and now < _metadata[1]:
        return _metadata[0]

    settings = get_settings()
    if not settings.oidc_discovery_url:
        raise OidcError("OIDC_DISCOVERY_URL is not configured.")

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        response = await client.get(settings.oidc_discovery_url)
        response.raise_for_status()
        doc = response.json()

    try:
        meta = ProviderMetadata(
            issuer=doc["issuer"],
            authorization_endpoint=doc["authorization_endpoint"],
            token_endpoint=doc["token_endpoint"],
            jwks_uri=doc["jwks_uri"],
            end_session_endpoint=doc.get("end_session_endpoint"),
            token_endpoint_auth_methods=tuple(
                doc.get("token_endpoint_auth_methods_supported", ["client_secret_post"])
            ),
        )
    except KeyError as exc:
        raise OidcError(f"Discovery document is missing {exc}.") from exc

    _metadata = (meta, now + _DISCOVERY_TTL)
    return meta


async def get_jwks(*, force: bool = False) -> Any:
    global _jwks
    now = time.monotonic()
    if not force and _jwks is not None and now < _jwks[1]:
        return _jwks[0]

    meta = await get_metadata()
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        response = await client.get(meta.jwks_uri)
        response.raise_for_status()
        keys = JsonWebKey.import_key_set(response.json())

    _jwks = (keys, now + _JWKS_TTL)
    return keys


# ---------------------------------------------------------------------------
# step 1 — redirect the browser to the provider
# ---------------------------------------------------------------------------
def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


async def build_authorization_url() -> tuple[str, dict[str, str]]:
    """Return the URL to redirect to, and the transaction state to remember."""
    settings = get_settings()
    meta = await get_metadata()

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier, challenge = _pkce_pair()

    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_uri,
        "scope": " ".join(settings.scope_list),
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = f"{meta.authorization_endpoint}?{urlencode(params)}"
    return url, {"state": state, "nonce": nonce, "verifier": verifier}


# ---------------------------------------------------------------------------
# step 2 — exchange the code and verify the ID token
# ---------------------------------------------------------------------------
async def exchange_code(code: str, verifier: str) -> dict[str, Any]:
    settings = get_settings()
    meta = await get_metadata()

    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.oidc_redirect_uri,
        "client_id": settings.oidc_client_id,
        "code_verifier": verifier,
    }
    use_basic = "client_secret_basic" in meta.token_endpoint_auth_methods
    if not use_basic:
        form["client_secret"] = settings.oidc_client_secret

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        if use_basic:
            response = await client.post(
                meta.token_endpoint,
                data=form,
                auth=httpx.BasicAuth(settings.oidc_client_id, settings.oidc_client_secret),
            )
        else:
            response = await client.post(meta.token_endpoint, data=form)

    if response.status_code != httpx.codes.OK:
        # The body can carry the client secret back in an error echo; log the
        # provider's error code only.
        detail = ""
        with contextlib.suppress(ValueError):
            detail = str(response.json().get("error", ""))
        raise OidcError(f"Token exchange failed ({response.status_code} {detail}).")

    payload = cast(dict[str, Any], response.json())
    if "id_token" not in payload:
        raise OidcError("Token response carried no id_token.")
    return payload


async def verify_id_token(id_token: str, nonce: str) -> dict[str, Any]:
    settings = get_settings()
    meta = await get_metadata()
    jwt = JsonWebToken(_ALLOWED_ALGS)

    claims_options = {
        "iss": {"essential": True, "values": [meta.issuer]},
        "aud": {"essential": True, "values": [settings.oidc_client_id]},
        "exp": {"essential": True},
        "nonce": {"essential": True, "values": [nonce]},
    }

    try:
        claims = jwt.decode(id_token, await get_jwks(), claims_options=claims_options)
        claims.validate(leeway=60)
    except (JoseError, ValueError):
        # A rotated signing key looks exactly like a bad signature. Refresh the
        # key set once and retry before rejecting the login.
        try:
            claims = jwt.decode(id_token, await get_jwks(force=True), claims_options=claims_options)
            claims.validate(leeway=60)
        except (JoseError, ValueError) as exc:
            raise OidcError(f"ID token rejected: {exc}") from exc

    return dict(claims)


def principal_from_claims(claims: dict[str, Any]) -> Principal:
    """Map provider claims onto our principal.

    Entra ID puts the address in `preferred_username` for work accounts and
    populates `email` only when the mail attribute is set, so both are tried.
    """
    email = str(claims.get("email") or claims.get("preferred_username") or claims.get("upn") or "")
    if not email:
        raise OidcError("ID token carried no email claim; cannot identify the user.")

    name = str(claims.get("name") or email.split("@", 1)[0])
    subject = str(claims.get("sub") or email)

    keep = ("sub", "email", "preferred_username", "name", "oid", "tid")
    raw = {k: str(claims[k]) for k in keep if k in claims}

    return Principal(subject=subject, email=email.lower(), name=name, raw_claims=raw)


def reset_caches() -> None:
    """Drop the discovery and JWKS caches. Used by tests."""
    global _metadata, _jwks
    _metadata = None
    _jwks = None
