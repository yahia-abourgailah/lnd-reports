from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError


def test_liveness_does_not_touch_the_database(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Postgres outage must not restart the API (NFR-03)."""

    def explode() -> bool:
        raise AssertionError("liveness consulted the database")

    monkeypatch.setattr("lnd.api.v1.health.db_ping", explode)

    response = client.get("/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_503_when_the_database_is_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def down() -> bool:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr("lnd.api.v1.health.db_ping", down)

    response = client.get("/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"] == "database unavailable"


def test_readiness_is_ok_when_the_database_answers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("lnd.api.v1.health.db_ping", lambda: True)

    response = client.get("/v1/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_summarises_each_component(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("lnd.api.v1.health.db_ping", lambda: True)
    monkeypatch.setattr("lnd.api.v1.health._check_redis", lambda: None)

    response = client.get("/v1/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["environment"] == "dev"
    assert body["components"]["database"]["status"] == "ok"
    assert body["components"]["redis"]["status"] == "ok"


def test_health_degrades_when_redis_is_down_but_postgres_is_up(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redis is a cache and a broker. Losing it slows the dashboard; it does not
    make the numbers wrong, so the API stays available."""
    monkeypatch.setattr("lnd.api.v1.health.db_ping", lambda: True)

    def redis_down() -> None:
        raise OSError("connection refused")

    monkeypatch.setattr("lnd.api.v1.health._check_redis", redis_down)

    response = client.get("/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["components"]["redis"]["status"] == "error"


def test_health_is_an_error_when_postgres_is_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def db_down() -> bool:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr("lnd.api.v1.health.db_ping", db_down)
    monkeypatch.setattr("lnd.api.v1.health._check_redis", lambda: None)

    response = client.get("/v1/health")
    assert response.status_code == 503
    assert response.json()["status"] == "error"


def test_every_response_carries_a_request_id(client: TestClient) -> None:
    response = client.get("/v1/health/live")
    assert response.headers["X-Request-ID"]


def test_security_headers_are_present(client: TestClient) -> None:
    response = client.get("/v1/health/live")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
