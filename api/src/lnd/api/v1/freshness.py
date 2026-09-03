"""GET /v1/freshness — how current the data is, per source and entity.

Authenticated, unlike /v1/health. Health says whether the platform is up, which
a load balancer needs to know before anyone has signed in. Freshness describes
the state of internal systems — which sources exist, which are failing, how far
behind each one is — and that is for people who are already through the door.

The freshness badge on every screen (FR-F03) reads this, so the response carries
the threshold it was computed against rather than making the client hardcode 60
minutes in a second place.

Always 200. A stale platform is a platform that is still serving, deliberately
(NFR-03) — turning staleness into an error status would make the endpoint
useless to the badge that has to render it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from lnd.auth.dependencies import CurrentUser
from lnd.db import get_db
from lnd.sync.freshness import FreshnessResponse, platform_freshness

router = APIRouter(tags=["freshness"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/freshness", response_model=FreshnessResponse)
def freshness(_user: CurrentUser, session: DbSession) -> FreshnessResponse:
    return platform_freshness(session)
