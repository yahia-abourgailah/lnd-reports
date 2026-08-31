from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

BASE_ENV = {
    "ENVIRONMENT": "dev",
    "SESSION_SECRET": "test-secret-that-is-long-enough-for-the-guard-check",
    "DATABASE_URL": "postgresql+psycopg://test:test@localhost:5432/test",
    "REDIS_URL": "redis://localhost:6379/0",
    "API_BASE_URL": "http://testserver",
    "WEB_BASE_URL": "http://testserver",
    "OIDC_REDIRECT_URI": "http://testserver/v1/auth/callback",
    "AUTH_DEV_BYPASS": "false",
}


def _reset_caches() -> None:
    from lnd import db
    from lnd.auth import oidc, session
    from lnd.config import get_settings

    get_settings.cache_clear()
    oidc.reset_caches()
    session._dev_secret = None
    db.dispose_engine()


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every test starts from a known, dev-shaped environment."""
    for key in list(os.environ):
        if key.split("_")[0] in {"ENVIRONMENT", "SESSION", "OIDC", "AUTH", "DATABASE", "REDIS"}:
            monkeypatch.delenv(key, raising=False)
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)

    _reset_caches()
    yield
    _reset_caches()


@pytest.fixture
def client() -> Iterator[TestClient]:
    from lnd.main import create_app

    with TestClient(create_app(), follow_redirects=False) as test_client:
        yield test_client


@pytest.fixture
def dev_bypass_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A client whose API has the development sign-in shortcut enabled."""
    monkeypatch.setenv("AUTH_DEV_BYPASS", "true")
    monkeypatch.setenv("AUTH_DEV_USER_EMAIL", "specialist@example.com")
    monkeypatch.setenv("AUTH_DEV_USER_NAME", "L&D Specialist")
    _reset_caches()

    from lnd.main import create_app

    with TestClient(create_app(), follow_redirects=False) as test_client:
        yield test_client
