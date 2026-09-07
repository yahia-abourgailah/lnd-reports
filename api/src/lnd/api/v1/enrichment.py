"""/v1/enrichment — the decisions people layer over what the sources say.

Read, author, retire, and read the history of any of them. Four verbs and no
update, because there is no update: a change supersedes and inserts, so the
audit trail is a property of the storage rather than a log somebody has to
remember to write.

`authored_by` comes from the signed-in session and is never read from the body.
An audit trail a caller can address to somebody else is not an audit trail.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lnd.auth.dependencies import CurrentUser
from lnd.db import get_db
from lnd.enrichment import (
    EnrichmentConflict,
    OverlayEntry,
    OverlayKind,
    author,
    history,
    live,
    retire,
)
from lnd.metrics.cache import invalidate as invalidate_metrics_cache

router = APIRouter(prefix="/enrichment", tags=["enrichment"])

DbSession = Annotated[Session, Depends(get_db)]


class EntryOut(BaseModel):
    id: int
    kind: str
    key: dict[str, Any]
    values: dict[str, Any]
    authored_by: str
    authored_at: dt.datetime
    superseded_at: dt.datetime | None
    is_live: bool
    note: str | None


class WriteRequest(BaseModel):
    """A decision, and the reason for it.

    `note` is optional but asked for everywhere it is offered. The tables hold
    judgement calls — which spelling is one trainer, which department a
    customised program was built for — and a judgement without its reasoning is
    one nobody can revisit.
    """

    key: dict[str, Any]
    values: dict[str, Any]
    note: str | None = Field(default=None, max_length=2000)


class RetireRequest(BaseModel):
    key: dict[str, Any]
    note: str | None = Field(default=None, max_length=2000)


class EntriesResponse(BaseModel):
    kind: str
    entries: list[EntryOut]


def _out(entry: OverlayEntry) -> EntryOut:
    return EntryOut(
        id=entry.id,
        kind=entry.kind.value,
        key=entry.key,
        values=entry.values,
        authored_by=entry.authored_by,
        authored_at=entry.authored_at,
        superseded_at=entry.superseded_at,
        is_live=entry.is_live,
        note=entry.note,
    )


@router.get("/{kind}", response_model=EntriesResponse)
def list_live(kind: OverlayKind, session: DbSession, _user: CurrentUser) -> EntriesResponse:
    """Everything currently in force for one overlay table."""
    return EntriesResponse(kind=kind.value, entries=[_out(e) for e in live(session, kind)])


@router.post("/{kind}/history", response_model=EntriesResponse)
def read_history(
    kind: OverlayKind, request: RetireRequest, session: DbSession, _user: CurrentUser
) -> EntriesResponse:
    """Every value one key has ever had, oldest first.

    POST rather than GET because the key is a composite object rather than a
    path segment — `(survey_id, question_id)` does not fit a URL without
    inventing an encoding both ends have to agree on.
    """
    try:
        entries = history(session, kind, request.key)
    except EnrichmentConflict as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return EntriesResponse(kind=kind.value, entries=[_out(e) for e in entries])


@router.put("/{kind}", response_model=EntryOut, status_code=200)
def put(
    kind: OverlayKind, request: WriteRequest, session: DbSession, user: CurrentUser
) -> EntryOut:
    """Set a value, superseding whatever was in force."""
    try:
        entry = author(
            session,
            kind,
            key=request.key,
            values=request.values,
            authored_by=user.email,
            note=request.note,
        )
    except EnrichmentConflict as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    # `get_db` commits nothing — read paths must not, and until this route
    # there were no write paths. Explicit, and before the cache is bumped: a
    # generation bumped ahead of the commit lets a request in flight repopulate
    # the new generation from the old overlay and pin it there.
    session.commit()

    # The overlay is an input to the transform, so a cached figure computed
    # under the old value is now wrong. Bumping here makes the dashboard correct
    # on the next request rather than at the next transform — which would be up
    # to half an hour of somebody watching their own edit not take effect.
    #
    # `core` itself still carries the old value until the transform runs. That
    # is visible rather than hidden: the figures move when the transform does,
    # and the enrichment screen says so.
    invalidate_metrics_cache()
    return _out(entry)


@router.post("/{kind}/retire", response_model=EntryOut | None)
def withdraw(
    kind: OverlayKind, request: RetireRequest, session: DbSession, user: CurrentUser
) -> EntryOut | None:
    """Withdraw an override so the source value applies again.

    Returns null when nothing was in force. Not an error: retiring an override
    that is already gone is the state the caller asked for.
    """
    try:
        entry = retire(session, kind, key=request.key, authored_by=user.email, note=request.note)
    except EnrichmentConflict as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    session.commit()
    invalidate_metrics_cache()
    return _out(entry) if entry else None
