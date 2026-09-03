"""Version 1 of the HTTP surface. Every endpoint lives under /v1/ (NFR-14)."""

from fastapi import APIRouter

from lnd.api.v1 import freshness, health, raw
from lnd.auth.router import router as auth_router

router = APIRouter(prefix="/v1")
router.include_router(health.router)
router.include_router(auth_router)
router.include_router(freshness.router)
router.include_router(raw.router)

__all__ = ["router"]
