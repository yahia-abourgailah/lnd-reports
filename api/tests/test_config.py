"""The startup guards.

These are the tests that matter most in week 1: each one asserts that a
misconfiguration stops the process rather than quietly serving something unsafe.
"""

from __future__ import annotations

import pytest

from lnd.config import Environment, Settings


def _settings(**overrides: str) -> Settings:
    base = {
        "environment": "production",
        "session_secret": "a" * 40,
        "oidc_discovery_url": "https://idp.example.com/.well-known/openid-configuration",
        "oidc_client_id": "client",
        "oidc_client_secret": "secret",
        "oidc_redirect_uri": "https://lnd.example.com/v1/auth/callback",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_production_settings_are_accepted() -> None:
    settings = _settings()
    assert settings.is_production
    assert settings.cookies_require_https


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_dev_bypass_is_refused_outside_dev(environment: str) -> None:
    with pytest.raises(ValueError, match="permitted in dev only"):
        _settings(environment=environment, auth_dev_bypass="true")  # type: ignore[arg-type]


def test_dev_bypass_is_allowed_in_dev() -> None:
    settings = Settings(environment=Environment.DEV, auth_dev_bypass=True)
    assert settings.auth_dev_bypass


def test_session_secret_is_required_outside_dev() -> None:
    with pytest.raises(ValueError, match="SESSION_SECRET is required"):
        _settings(session_secret="")


def test_short_session_secret_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 32 characters"):
        _settings(session_secret="too-short")


def test_oidc_is_required_outside_dev() -> None:
    with pytest.raises(ValueError, match="no local passwords"):
        _settings(oidc_client_id="")


def test_production_redirect_uri_must_be_https() -> None:
    with pytest.raises(ValueError, match="must be https"):
        _settings(oidc_redirect_uri="http://lnd.example.com/v1/auth/callback")


def test_dev_does_not_require_oidc() -> None:
    """Week 1 has to proceed before the app registration exists (Q-14)."""
    settings = Settings(environment=Environment.DEV)
    assert not settings.oidc_configured
    assert not settings.cookies_require_https


def test_secrets_are_not_in_the_repr() -> None:
    """A settings object reaches logs and tracebacks; credentials must not."""
    settings = _settings()
    rendered = repr(settings)
    assert "secret" not in rendered.replace("session_secret", "").replace("oidc_client_secret", "")
    assert "a" * 40 not in rendered
