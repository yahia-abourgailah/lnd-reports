"""Signed session cookie.

Stateless: the cookie carries the claims and a signature, so there is no session
table to keep in step and no Redis lookup on every request. It is signed rather
than encrypted — nothing secret goes in it, only the subject, email and name the
IdP already asserted.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any, Final

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from lnd.auth.principal import Principal
from lnd.config import Environment, get_settings

log = logging.getLogger(__name__)

_SALT: Final = "lnd.session.v1"

# Short-lived cookie holding the in-flight authorization request (state, nonce,
# PKCE verifier). Separate from the session so a failed login can never be
# mistaken for a successful one.
TX_COOKIE: Final = "lnd_oidc_tx"
TX_SALT: Final = "lnd.oidc.tx.v1"
TX_MAX_AGE: Final = 600

_dev_secret: str | None = None


def _signing_key() -> str:
    """The session signing key.

    Outside dev a real SESSION_SECRET is mandatory and validated at startup.
    In dev an absent one is replaced by an ephemeral key so the stack comes up
    on a fresh clone; sessions then do not survive a restart, which is loud
    enough to be noticed and harmless enough not to matter.
    """
    global _dev_secret
    settings = get_settings()
    if settings.session_secret:
        return settings.session_secret

    if settings.environment is not Environment.DEV:  # pragma: no cover - guarded at startup
        raise RuntimeError("SESSION_SECRET is required outside dev.")

    if _dev_secret is None:
        _dev_secret = secrets.token_urlsafe(48)
        log.warning(
            "SESSION_SECRET is unset; generated an ephemeral dev key. "
            "Sessions will not survive a restart.",
            extra={"event": "session.ephemeral_key"},
        )
    return _dev_secret


def _serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_signing_key(), salt=salt)


def _cookie_kwargs() -> dict[str, Any]:
    settings = get_settings()
    return {
        "httponly": True,
        "secure": settings.cookies_require_https,
        # Lax, not Strict: the IdP redirects back with a top-level GET, and
        # Strict would drop the cookie on exactly that navigation.
        "samesite": "lax",
        "path": "/",
    }


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------
def write_session(response: Response, principal: Principal) -> None:
    settings = get_settings()
    token = _serializer(_SALT).dumps(principal.model_dump(mode="json"))
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_max_age_seconds,
        **_cookie_kwargs(),
    )


def read_session(request: Request) -> Principal | None:
    """Return the signed-in principal, or None. Never raises on a bad cookie."""
    settings = get_settings()
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        return None

    try:
        data = _serializer(_SALT).loads(raw, max_age=settings.session_max_age_seconds)
    except SignatureExpired:
        log.info("session expired", extra={"event": "session.expired"})
        return None
    except BadSignature:
        # Tampering, or a rotated signing key. Same response either way.
        log.warning("session signature rejected", extra={"event": "session.bad_signature"})
        return None

    try:
        return Principal.model_validate(data)
    except ValueError:  # pragma: no cover - only on a shape change mid-rollout
        log.warning("session payload rejected", extra={"event": "session.bad_payload"})
        return None


def clear_session(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(settings.session_cookie_name, path="/")


# ---------------------------------------------------------------------------
# in-flight authorization request
# ---------------------------------------------------------------------------
def write_tx(response: Response, payload: dict[str, str]) -> None:
    token = _serializer(TX_SALT).dumps(payload)
    response.set_cookie(TX_COOKIE, token, max_age=TX_MAX_AGE, **_cookie_kwargs())


def read_tx(request: Request) -> dict[str, str] | None:
    raw = request.cookies.get(TX_COOKIE)
    if not raw:
        return None
    try:
        data = _serializer(TX_SALT).loads(raw, max_age=TX_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return data if isinstance(data, dict) else None


def clear_tx(response: Response) -> None:
    response.delete_cookie(TX_COOKIE, path="/")
