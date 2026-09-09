"""Signing in, against a provider that does not trust us either.

Every other auth test in this suite stubs something: the token, the JWKS, the
discovery document. Each proves a branch. None of them proves the thing that
actually matters — that this platform can complete an authorization-code flow
with PKCE against a real OIDC provider — and until now that was the one endpoint
in the whole API which had never run end to end, because the Microsoft Entra
registration does not exist.

So `tests/idp` is a real provider: a discovery document, an authorization
endpoint that requires PKCE with S256, a token endpoint that verifies the code
verifier before issuing anything, an RS256-signed ID token, and a JWKS to check
it against. It refuses a wrong verifier and refuses a reused code, which is what
makes agreement here evidence rather than a restatement of our own assumptions.

WHAT THIS DOES NOT PROVE

That Entra is configured correctly. It proves the client is correct — the
protocol, the verification, the session — so when the registration arrives, a
failure is on their side of the line and there is a green test saying so.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tests import idp


@pytest.fixture
def provider() -> Iterator[str]:
    """A live identity provider on a loopback port, for one test."""
    server, issuer = idp.serve()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield issuer
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def signing_in(provider: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The API, configured for real single sign-on rather than the dev bypass."""
    monkeypatch.setenv("AUTH_DEV_BYPASS", "false")
    monkeypatch.setenv("OIDC_DISCOVERY_URL", f"{provider}/.well-known/openid-configuration")
    monkeypatch.setenv("OIDC_CLIENT_ID", idp.CLIENT_ID)
    monkeypatch.setenv("OIDC_CLIENT_SECRET", idp.CLIENT_SECRET)
    monkeypatch.setenv("OIDC_REDIRECT_URI", "http://testserver/v1/auth/callback")
    monkeypatch.setenv("API_BASE_URL", "http://testserver")
    monkeypatch.setenv("WEB_BASE_URL", "http://testserver")

    from lnd.auth import oidc
    from lnd.config import get_settings

    get_settings.cache_clear()
    oidc.reset_caches()

    from lnd.main import create_app

    with TestClient(create_app(), follow_redirects=False) as client:
        yield client

    get_settings.cache_clear()
    oidc.reset_caches()


def _authorize(client: TestClient) -> str:
    """Follow the redirect to the provider and come back with the callback URL."""
    import httpx

    start = client.get("/v1/auth/login")
    assert start.status_code == 303, start.text
    # The provider is a real server, so this leg is a real HTTP request rather
    # than a hop inside the test client.
    handoff = httpx.get(start.headers["location"], follow_redirects=False)
    assert handoff.status_code == 302, handoff.text
    return handoff.headers["location"]


class TestTheFlowCompletes:
    def test_signing_in_end_to_end(self, signing_in: TestClient) -> None:
        """Discovery, PKCE, code exchange, signature, nonce, session — all of it."""
        callback = _authorize(signing_in)
        landed = signing_in.get(callback.replace("http://testserver", ""))
        assert landed.status_code == 303, landed.text

        me = signing_in.get("/v1/auth/me")
        assert me.status_code == 200, me.text
        assert me.json()["email"] == idp.USER["email"]
        assert me.json()["subject"] == idp.USER["sub"]

    def test_the_authorization_request_carries_pkce(self, signing_in: TestClient) -> None:
        """The provider refuses anything else, so reaching the callback at all
        proves S256 was sent — but assert the parameters too, because a future
        change could satisfy the provider and weaken the request."""
        from urllib.parse import parse_qs, urlparse

        start = signing_in.get("/v1/auth/login")
        query = parse_qs(urlparse(start.headers["location"]).query)
        assert query["code_challenge_method"] == ["S256"]
        assert query["response_type"] == ["code"]
        assert query["nonce"] and query["state"]
        # The verifier itself must never appear in a URL the browser can read.
        assert "code_verifier" not in query

    def test_the_status_endpoint_stops_saying_dev_bypass(self, signing_in: TestClient) -> None:
        assert signing_in.get("/v1/auth/status").json()["mode"] == "oidc"


class TestTheFlowRefusesWhatItShould:
    """A refused login redirects; it does not 400.

    That is deliberate and it took a failing test to notice: the browser lands
    back on the application with `?auth_error=<reason>`, because somebody whose
    sign-in failed needs the app and an explanation, not a JSON body. The detail
    goes to the log and never into the query string.

    So what these assert is the pair that matters — the reason reaches the front
    end, and **no session is issued**.
    """

    @staticmethod
    def _refused(response: object, reason: str) -> None:
        assert response.status_code == 303, response.text  # type: ignore[attr-defined]
        assert f"auth_error={reason}" in response.headers["location"]  # type: ignore[attr-defined]

    def test_a_tampered_state_is_refused(self, signing_in: TestClient) -> None:
        """State binds the callback to the request that started it. Without the
        check, an attacker's code can be delivered into somebody's session."""
        callback = _authorize(signing_in)
        tampered = callback.replace("http://testserver", "").replace("state=", "state=x")
        self._refused(signing_in.get(tampered), "state_mismatch")
        assert signing_in.get("/v1/auth/me").status_code == 401

    def test_a_code_cannot_be_replayed(self, signing_in: TestClient) -> None:
        """The provider drops a code the moment it is exchanged, and the second
        attempt fails verification rather than issuing a second session."""
        callback = _authorize(signing_in)
        path = callback.replace("http://testserver", "")
        assert signing_in.get(path).status_code == 303

        signing_in.post("/v1/auth/logout")
        again = signing_in.get(path)
        assert again.status_code == 303
        assert "auth_error=" in again.headers["location"]
        assert signing_in.get("/v1/auth/me").status_code == 401

    def test_no_in_flight_request_is_refused(self, signing_in: TestClient) -> None:
        """A callback arriving with no transaction cookie is not a login."""
        self._refused(signing_in.get("/v1/auth/callback?code=whatever&state=whatever"), "expired")
        assert signing_in.get("/v1/auth/me").status_code == 401

    def test_a_provider_error_is_not_a_session(self, signing_in: TestClient) -> None:
        self._refused(signing_in.get("/v1/auth/callback?error=access_denied"), "provider_error")
        assert signing_in.get("/v1/auth/me").status_code == 401


class TestTheSession:
    def test_signing_out_ends_it(self, signing_in: TestClient) -> None:
        callback = _authorize(signing_in)
        signing_in.get(callback.replace("http://testserver", ""))
        assert signing_in.get("/v1/auth/me").status_code == 200

        assert signing_in.post("/v1/auth/logout").status_code == 200
        assert signing_in.get("/v1/auth/me").status_code == 401

    def test_the_session_reaches_the_rest_of_the_api(self, signing_in: TestClient) -> None:
        """A cookie that authenticates `/auth/me` and nothing else would be a
        session in name only.

        `/exceptions/rules` because it needs the session and needs no database:
        this test is about the cookie, and pointing it at an endpoint that
        queries would make a missing database look like a broken login.
        """
        assert signing_in.get("/v1/exceptions/rules").status_code == 401

        callback = _authorize(signing_in)
        signing_in.get(callback.replace("http://testserver", ""))
        assert signing_in.get("/v1/exceptions/rules").status_code == 200
