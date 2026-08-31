"""Login, callback, logout and whoami.

GET /v1/auth/login      → redirect to the IdP
GET /v1/auth/callback   → exchange, verify, set the session cookie
POST /v1/auth/logout    → clear the session
GET /v1/auth/me         → the signed-in user
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode, urljoin, urlparse

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from lnd.auth import oidc
from lnd.auth.dependencies import CurrentUser
from lnd.auth.principal import Principal
from lnd.auth.session import clear_session, clear_tx, read_tx, write_session, write_tx
from lnd.config import get_settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class MeResponse(BaseModel):
    email: str
    name: str
    subject: str


class AuthStatus(BaseModel):
    authenticated: bool
    mode: str
    login_url: str


def _safe_next(candidate: str | None) -> str:
    """Only ever redirect to our own front end.

    An open redirect on the callback would let a crafted login link bounce a
    signed-in user to somebody else's page, so anything absolute or
    protocol-relative is discarded rather than sanitised.
    """
    base = get_settings().web_base_url
    if not candidate:
        return base
    if not candidate.startswith("/") or candidate.startswith("//"):
        return base
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return base
    return urljoin(base if base.endswith("/") else base + "/", candidate.lstrip("/"))


def _failure(reason: str, detail: str) -> RedirectResponse:
    """Send the browser back to the front end with a machine-readable reason.

    The detail goes to the log, never to the query string.
    """
    log.warning("login failed: %s", detail, extra={"event": "auth.login_failed", "reason": reason})
    settings = get_settings()
    target = f"{settings.web_base_url}/?{urlencode({'auth_error': reason})}"
    response = RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    clear_tx(response)
    return response


@router.get("/status", response_model=AuthStatus)
def auth_status(request: Request) -> AuthStatus:
    """Whether this browser is signed in, and how to sign in if not.

    Unauthenticated on purpose: the SPA calls it before it has a session.
    """
    from lnd.auth.session import read_session

    settings = get_settings()
    mode = "dev-bypass" if settings.auth_dev_bypass else "oidc"
    return AuthStatus(
        authenticated=read_session(request) is not None,
        mode=mode,
        login_url=f"{settings.api_base_url}/v1/auth/login",
    )


@router.get("/login")
async def login(request: Request, next: str | None = None) -> Response:
    """Begin sign-in.

    In dev, with no app registration yet (Q-14), AUTH_DEV_BYPASS issues a
    session directly. The setting is refused at startup outside dev, so this
    branch cannot exist anywhere a real person could reach.
    """
    settings = get_settings()
    destination = _safe_next(next)

    if settings.auth_dev_bypass:
        principal = Principal(
            subject="dev-bypass",
            email=settings.auth_dev_user_email,
            name=settings.auth_dev_user_name,
        )
        response = RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)
        write_session(response, principal)
        log.warning(
            "issued a session via the development bypass",
            extra={"event": "auth.dev_bypass", "email": principal.email},
        )
        return response

    try:
        url, tx = await oidc.build_authorization_url()
    except oidc.OidcError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Single sign-on is not configured.",
        ) from exc

    response = RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)
    write_tx(response, {**tx, "next": destination})
    return response


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> Response:
    """Finish sign-in: verify state, exchange the code, verify the ID token."""
    if error:
        return _failure("provider_error", f"{error}: {error_description or ''}")

    tx = read_tx(request)
    if tx is None:
        return _failure("expired", "no in-flight authorization request (cookie missing or stale)")

    if not code or not state:
        return _failure("invalid_response", "callback carried no code or no state")

    # Constant-time-ish comparison is unnecessary here — state is single-use and
    # already bound to a signed cookie — but the check itself is essential.
    if state != tx.get("state"):
        return _failure("state_mismatch", "state did not match the in-flight request")

    try:
        tokens = await oidc.exchange_code(code, tx["verifier"])
        claims = await oidc.verify_id_token(tokens["id_token"], tx["nonce"])
        principal = oidc.principal_from_claims(claims)
    except oidc.OidcError as exc:
        return _failure("verification_failed", str(exc))
    except Exception as exc:  # never leak provider internals to the browser
        return _failure("unavailable", f"identity provider unreachable: {exc!r}")

    response = RedirectResponse(_safe_next(tx.get("next")), status_code=status.HTTP_303_SEE_OTHER)
    write_session(response, principal)
    clear_tx(response)
    log.info(
        "signed in",
        extra={"event": "auth.login", "subject": principal.subject, "email": principal.email},
    )
    return response


@router.post("/logout")
def logout() -> Response:
    """Clear the session.

    Local only. RP-initiated logout at the provider is deliberately not called:
    signing the user out of every other company application because they closed
    a dashboard is not what anyone means by "log out".
    """
    response = JSONResponse({"status": "signed_out"})
    clear_session(response)
    return response


@router.get("/me", response_model=MeResponse)
def me(user: CurrentUser) -> MeResponse:
    return MeResponse(email=user.email, name=user.name, subject=user.subject)
