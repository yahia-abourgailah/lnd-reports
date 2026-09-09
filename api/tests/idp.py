"""A real OIDC provider, small enough to read, for proving the login flow.

NOT a mock of our client. It speaks the protocol: a discovery document, an
authorization endpoint that enforces PKCE, a token endpoint that verifies the
code verifier, an RS256-signed ID token and a JWKS to check it against.

That distinction is the whole point. A test that stubs `verify_id_token` proves
we call a function; this proves the platform completes an authorization-code
flow with PKCE against something that does not trust it either — the signature
is real, the nonce is echoed, and a wrong verifier is refused.

It exists because the Microsoft Entra registration does not, and the callback
route was the one endpoint in the platform that had never run end to end. When
Entra arrives this becomes redundant for staging and stays useful in CI, where
there is no identity provider and never will be.

Imported by `test_oidc_flow`, and runnable on its own —
`python -m tests.idp 9500` — for a manual rehearsal against a browser.

Nothing here is fit for any other purpose: it issues a token to whoever asks.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from authlib.jose import JsonWebKey, jwt

CLIENT_ID = "lnd-analytics"
CLIENT_SECRET = "test-secret"

KEY = JsonWebKey.generate_key("RSA", 2048, is_private=True)
KID = "test-key-1"
PUBLIC = {**KEY.as_dict(is_private=False), "kid": KID, "use": "sig", "alg": "RS256"}

USER = {
    "sub": "8f2c1e00-aaaa-4bbb-9ccc-000000000001",
    "email": "ld.specialist@theaddressholding.com",
    "name": "L&D Specialist",
}

#: Set when the server binds, because the issuer has to name the port it is
#: actually listening on — a test picks port 0 and is told which one it got.
ISSUER = ""

#: code -> what the authorization request asked for
CODES: dict[str, dict[str, str]] = {}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, kind: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode())

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"  idp  {fmt % args}", flush=True)

    # -- GET -------------------------------------------------------------
    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}

        if url.path == "/.well-known/openid-configuration":
            self._json(
                200,
                {
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                    "end_session_endpoint": f"{ISSUER}/logout",
                    "response_types_supported": ["code"],
                    "subject_types_supported": ["public"],
                    "id_token_signing_alg_values_supported": ["RS256"],
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["client_secret_post"],
                },
            )
            return

        if url.path == "/jwks":
            self._json(200, {"keys": [PUBLIC]})
            return

        if url.path == "/authorize":
            missing = [
                k
                for k in ("client_id", "redirect_uri", "state", "nonce", "code_challenge")
                if k not in query
            ]
            if missing:
                self._json(400, {"error": "invalid_request", "missing": missing})
                return
            if query["client_id"] != CLIENT_ID:
                self._json(400, {"error": "unauthorized_client"})
                return
            if query.get("code_challenge_method") != "S256":
                self._json(400, {"error": "invalid_request", "detail": "PKCE S256 required"})
                return

            code = secrets.token_urlsafe(24)
            CODES[code] = {
                "nonce": query["nonce"],
                "challenge": query["code_challenge"],
                "redirect_uri": query["redirect_uri"],
            }
            # A real provider shows a login page here. This one consents
            # immediately and redirects, which is the only shortcut it takes.
            self.send_response(302)
            self.send_header(
                "Location",
                query["redirect_uri"] + "?" + urlencode({"code": code, "state": query["state"]}),
            )
            self.end_headers()
            return

        self._json(404, {"error": "not_found"})

    # -- POST ------------------------------------------------------------
    def do_POST(self) -> None:
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}

        if url.path != "/token":
            self._json(404, {"error": "not_found"})
            return

        code = form.get("code", "")
        record = CODES.pop(code, None)
        if record is None:
            self._json(400, {"error": "invalid_grant", "detail": "unknown or reused code"})
            return
        if form.get("client_id") != CLIENT_ID or form.get("client_secret") != CLIENT_SECRET:
            self._json(401, {"error": "invalid_client"})
            return

        # PKCE, verified rather than accepted: the challenge is the SHA-256 of
        # the verifier the client kept to itself.
        verifier = form.get("code_verifier", "")
        digest = hashlib.sha256(verifier.encode()).digest()
        expected = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        if expected != record["challenge"]:
            self._json(400, {"error": "invalid_grant", "detail": "PKCE verifier does not match"})
            return

        now = int(time.time())
        id_token = jwt.encode(
            {"alg": "RS256", "kid": KID},
            {
                "iss": ISSUER,
                "sub": USER["sub"],
                "aud": CLIENT_ID,
                "iat": now,
                "exp": now + 300,
                "nonce": record["nonce"],
                "email": USER["email"],
                "name": USER["name"],
                "preferred_username": USER["email"],
            },
            KEY,
        ).decode()
        self._json(
            200,
            {
                "access_token": secrets.token_urlsafe(24),
                "token_type": "Bearer",
                "expires_in": 300,
                "id_token": id_token,
            },
        )


def serve(port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """Bind, publish the issuer, and hand back the server for a thread to run.

    Port 0 by default: a test that hardcodes a port fails on a machine where
    something else already has it, and that failure looks like a bug in the
    thing under test.
    """
    global ISSUER
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    ISSUER = f"http://127.0.0.1:{server.server_address[1]}"
    CODES.clear()
    return server, ISSUER


if __name__ == "__main__":
    server, issuer = serve(int(sys.argv[1]) if len(sys.argv) > 1 else 9500)
    print(f"test identity provider on {issuer}", flush=True)
    server.serve_forever()
