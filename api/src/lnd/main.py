"""FastAPI application factory.

Imported by gunicorn as `lnd.main:app` for the `api` service, and by nothing
else — the worker and beat enter through `lnd.worker.celery_app`, on the same
image and the same code.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lnd import __version__
from lnd.api.v1 import router as v1_router
from lnd.config import Environment, get_settings
from lnd.db import dispose_engine
from lnd.logging import configure_logging
from lnd.middleware import RequestContextMiddleware, SecurityHeadersMiddleware

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    log.info(
        "starting",
        extra={
            "event": "app.start",
            "version": __version__,
            "environment": str(settings.environment),
            "auth_mode": "dev-bypass" if settings.auth_dev_bypass else "oidc",
        },
    )
    # Deliberately no database call here. A Postgres blip during a deploy must
    # not stop the API coming up; readiness reports it instead (NFR-03).
    yield
    dispose_engine()
    log.info("stopped", extra={"event": "app.stop"})


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="L&D Analytics Platform",
        version=__version__,
        description=(
            "Read-only analytics over the CRM, Microsoft Forms, the HRIS and "
            "LinkedIn Learning. No endpoint writes to any source system."
        ),
        lifespan=lifespan,
        # The schema is internal documentation, not a public contract.
        docs_url=None if settings.is_production else "/v1/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/v1/openapi.json",
    )

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)

    # In dev the SPA is served by Vite on its own origin, so the session cookie
    # needs an explicit CORS grant. Everywhere else `proxy` puts the API and the
    # bundle on one origin and no cross-origin request is legitimate.
    if settings.environment is Environment.DEV:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[settings.web_base_url],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(v1_router)
    return app


app = create_app()
