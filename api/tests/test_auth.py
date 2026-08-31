"""Authentication behaviour.

The security-relevant assertions here are the ones about what is *refused*: an
unsigned session, a callback with no in-flight transaction, a mismatched state,
and a redirect target that is not ours.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lnd.auth.principal import Principal


# ---------------------------------------------------------------- unauthenticated
def test_me_requires_a_session(client: TestClient) -> None:
    assert client.get("/v1/auth/me").status_code == 401


def test_status_is_reachable_without_a_session(client: TestClient) -> None:
    """The SPA calls this before it has one."""
    response = client.get("/v1/auth/status")
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is False
    assert body["mode"] == "oidc"
    assert body["login_url"].endswith("/v1/auth/login")


def test_login_is_unavailable_when_sso_is_not_configured(client: TestClient) -> None:
    """Dev without an app registration and without the bypass: a clear 503,
    not a stack trace."""
    assert client.get("/v1/auth/login").status_code == 503


# ------------------------------------------------------------------ dev bypass
def test_dev_bypass_signs_in_and_persists(dev_bypass_client: TestClient) -> None:
    login = dev_bypass_client.get("/v1/auth/login")
    assert login.status_code == 303
    assert "lnd_session" in login.cookies

    me = dev_bypass_client.get("/v1/auth/me")
    assert me.status_code == 200
    assert me.json() == {
        "email": "specialist@example.com",
        "name": "L&D Specialist",
        "subject": "dev-bypass",
    }


def test_dev_bypass_reports_its_mode(dev_bypass_client: TestClient) -> None:
    assert dev_bypass_client.get("/v1/auth/status").json()["mode"] == "dev-bypass"


def test_logout_clears_the_session(dev_bypass_client: TestClient) -> None:
    dev_bypass_client.get("/v1/auth/login")
    assert dev_bypass_client.get("/v1/auth/me").status_code == 200

    assert dev_bypass_client.post("/v1/auth/logout").status_code == 200
    assert dev_bypass_client.get("/v1/auth/me").status_code == 401


def test_session_cookie_is_httponly_and_lax(dev_bypass_client: TestClient) -> None:
    response = dev_bypass_client.get("/v1/auth/login")
    header = response.headers["set-cookie"]
    assert "HttpOnly" in header
    assert "SameSite=lax" in header.lower().replace("samesite=lax", "SameSite=lax")


# --------------------------------------------------------------- open redirect
@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example.com/steal",
        "//evil.example.com/steal",
        "http://evil.example.com",
    ],
)
def test_login_refuses_an_external_next(dev_bypass_client: TestClient, target: str) -> None:
    response = dev_bypass_client.get("/v1/auth/login", params={"next": target})
    assert response.status_code == 303
    assert "evil.example.com" not in response.headers["location"]


def test_login_honours_a_relative_next(dev_bypass_client: TestClient) -> None:
    response = dev_bypass_client.get("/v1/auth/login", params={"next": "/coverage"})
    assert response.headers["location"] == "http://testserver/coverage"


# ------------------------------------------------------------------- callback
def test_callback_without_a_transaction_is_rejected(client: TestClient) -> None:
    response = client.get("/v1/auth/callback", params={"code": "abc", "state": "xyz"})
    assert response.status_code == 303
    assert "auth_error=expired" in response.headers["location"]


def test_callback_rejects_a_mismatched_state(client: TestClient) -> None:
    from lnd.auth.session import TX_COOKIE, TX_SALT, _serializer

    token = _serializer(TX_SALT).dumps(
        {"state": "real-state", "nonce": "n", "verifier": "v", "next": "/"}
    )
    client.cookies.set(TX_COOKIE, token)

    response = client.get("/v1/auth/callback", params={"code": "abc", "state": "forged"})
    assert response.status_code == 303
    assert "auth_error=state_mismatch" in response.headers["location"]


def test_callback_passes_the_provider_error_through_as_a_reason(client: TestClient) -> None:
    response = client.get("/v1/auth/callback", params={"error": "access_denied"})
    assert response.status_code == 303
    assert "auth_error=provider_error" in response.headers["location"]


# ------------------------------------------------------------------- sessions
def test_a_tampered_session_is_not_accepted(dev_bypass_client: TestClient) -> None:
    dev_bypass_client.get("/v1/auth/login")
    good = dev_bypass_client.cookies["lnd_session"]

    dev_bypass_client.cookies.set("lnd_session", good[:-4] + "AAAA")
    assert dev_bypass_client.get("/v1/auth/me").status_code == 401


def test_an_unsigned_session_is_not_accepted(client: TestClient) -> None:
    import base64
    import json

    forged = base64.urlsafe_b64encode(
        json.dumps({"subject": "x", "email": "attacker@example.com", "name": "X"}).encode()
    ).decode()
    client.cookies.set("lnd_session", forged)
    assert client.get("/v1/auth/me").status_code == 401


def test_a_session_signed_with_another_key_is_not_accepted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from itsdangerous import URLSafeTimedSerializer

    from lnd.auth.session import _SALT

    other = URLSafeTimedSerializer("a-completely-different-signing-key", salt=_SALT)
    principal = Principal(subject="x", email="attacker@example.com", name="X")
    client.cookies.set("lnd_session", other.dumps(principal.model_dump(mode="json")))

    assert client.get("/v1/auth/me").status_code == 401


def test_a_session_signed_with_the_wrong_salt_is_not_accepted(client: TestClient) -> None:
    """The transaction cookie and the session cookie share a key but not a salt,
    so one can never be replayed as the other."""
    from lnd.auth.session import TX_SALT, _serializer

    principal = Principal(subject="x", email="attacker@example.com", name="X")
    client.cookies.set("lnd_session", _serializer(TX_SALT).dumps(principal.model_dump(mode="json")))

    assert client.get("/v1/auth/me").status_code == 401
