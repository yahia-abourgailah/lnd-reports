from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

# Captured at import, before the autouse fixture below replaces DATABASE_URL
# with a deliberately unreachable one. Tests that need a real database read
# this instead; without it they skip rather than fail.
#   make test  supplies it from .env; CI supplies its service container.
REAL_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

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


# ---------------------------------------------------------------------------
# database
# ---------------------------------------------------------------------------
# The landing layer's guarantees — idempotency, append-only history, replay —
# are properties of PostgreSQL, not of Python. Testing them against a stub would
# test the stub. So these run against a real database or they do not run at all.
@pytest.fixture(scope="session")
def db_engine():  # type: ignore[no-untyped-def]
    from sqlalchemy import create_engine, text

    if not REAL_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set; run `make test` with the dev stack up")

    engine = create_engine(REAL_DATABASE_URL, future=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment, not logic
        engine.dispose()
        pytest.skip(f"database at TEST_DATABASE_URL is unreachable: {exc}")

    from lnd.db import SCHEMAS, Base
    from lnd.ingest.models import RawRecord

    with engine.begin() as connection:
        for schema in SCHEMAS:
            connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
    Base.metadata.create_all(engine, tables=[RawRecord.__table__], checkfirst=True)

    yield engine
    engine.dispose()


@pytest.fixture
def db(db_engine) -> Iterator:  # type: ignore[no-untyped-def, type-arg]
    """A session inside a transaction that is always rolled back.

    Each test therefore sees empty tables and leaves nothing behind.
    """
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    connection = db_engine.connect()
    transaction = connection.begin()

    # Start from empty. A dev database carries rows from real syncs, and a test
    # asserting "exactly one sync run" would otherwise pass or fail depending on
    # what someone did in the terminal an hour ago. TRUNCATE is transactional in
    # PostgreSQL, so the rollback below puts every existing row back.
    connection.execute(text("TRUNCATE raw.source_record RESTART IDENTITY"))

    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
