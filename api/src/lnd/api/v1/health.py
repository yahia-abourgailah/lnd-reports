"""Health and readiness.

Three endpoints, because they answer different questions and get different
answers when a dependency is down:

    /v1/health/live   is this process alive?          container restart policy
    /v1/health/ready  can it serve a request?         proxy and deploy gating
    /v1/health        what is the state of each part? humans and the CI smoke test

All three are unauthenticated. They disclose no data — only whether a component
answers — and the readiness probe has to work before anyone has signed in.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Literal

import redis
from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from lnd import __version__
from lnd.config import get_settings
from lnd.db import ping as db_ping

log = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])

Status = Literal["ok", "degraded", "error"]


class ComponentHealth(BaseModel):
    status: Status
    latency_ms: float | None = None
    detail: str | None = None


class HealthResponse(BaseModel):
    status: Status
    version: str
    environment: str
    components: dict[str, ComponentHealth]


def _timed(check: Callable[[], object], name: str) -> ComponentHealth:
    started = time.perf_counter()
    try:
        check()
    except (SQLAlchemyError, redis.RedisError, OSError) as exc:
        log.warning("health check failed", extra={"event": "health.failed", "component": name})
        return ComponentHealth(status="error", detail=type(exc).__name__)
    return ComponentHealth(status="ok", latency_ms=round((time.perf_counter() - started) * 1000, 2))


def _check_redis() -> None:
    client = redis.Redis.from_url(get_settings().redis_url, socket_connect_timeout=2)
    try:
        client.ping()
    finally:
        client.close()


@router.get("/live", status_code=status.HTTP_200_OK)
def live() -> dict[str, str]:
    """Liveness: the process is up. No dependency is consulted.

    A database outage must never restart the API — the dashboard is required to
    keep serving last-known-good data through a source or store failure
    (NFR-03).
    """
    return {"status": "ok"}


@router.get("/ready")
def ready(response: Response) -> dict[str, str]:
    """Readiness: this instance can serve a request that touches the database."""
    try:
        db_ping()
    except (SQLAlchemyError, OSError):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "error", "detail": "database unavailable"}
    return {"status": "ok"}


@router.get("", response_model=HealthResponse)
@router.get("/", response_model=HealthResponse, include_in_schema=False)
def health(response: Response) -> HealthResponse:
    settings = get_settings()
    components = {
        "database": _timed(db_ping, "database"),
        "redis": _timed(_check_redis, "redis"),
    }

    overall: Status = "ok" if all(c.status == "ok" for c in components.values()) else "degraded"
    if components["database"].status != "ok":
        overall = "error"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status=overall,
        version=__version__,
        environment=str(settings.environment),
        components=components,
    )
