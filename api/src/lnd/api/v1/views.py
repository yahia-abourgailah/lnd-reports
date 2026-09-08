"""Saved views: a named filter set, restored by navigating to it.

A specialist who asks "MarQ, this quarter, by department" every month is
re-picking the same six controls every month. This is the bookmark for it, and
it is a bookmark in the literal sense — what is stored is the path and the query
string, because `web/src/filters.ts` already keeps the filter state in the URL.
Restoring a view is navigation. There is no second representation of a filter
set that could drift from the first.

THE QUERY IS PARSED BEFORE IT IS STORED

A saved view holding a parameter the API refuses is a bookmark that fails a
month later, in front of the person who saved it. So the query string is read
through the same shape every endpoint reads — unknown keys are refused, values
are coerced, and the scope sentence is resolved once, at save time, from the
parsed filters rather than from the raw string.

That sentence is why the list reads as sentences. "sector in Finance, Sales;
2026-01-01 to 2026-03-31" is a thing somebody recognises; `?sector=Finance&
sector=Sales&date_from=2026-01-01` is a thing they have to decode.

SHARED, AND OWNED

Everybody who can sign in is L&D — one permission set in v1 — and a view called
"what we present to the board" is worth more shared than hidden. So the list is
everyone's, with the author on each row. Renaming and deleting stay with
whoever made it, so a shared list cannot be quietly rearranged by somebody else.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lnd.auth.dependencies import CurrentUser
from lnd.db import get_db
from lnd.metrics.filters import MetricFilters
from lnd.models.app_ import SavedView

router = APIRouter(prefix="/views", tags=["views"])

DbSession = Annotated[Session, Depends(get_db)]

#: The screens a view may point at. An allowlist rather than free text: the
#: path is handed straight to the router when a view is opened, and "wherever
#: the client says" is how a stored string becomes an open redirect.
PATHS: frozenset[str] = frozenset(
    {"/", "/coverage", "/funnel", "/programs", "/trainers", "/learners"}
)

#: Query keys, mapped to the `MetricFilters` field each fills. Every one of
#: these is a parameter of `lnd.api.v1.kpis._filters`, and `test_saved_views`
#: asserts that the two sets are identical — a filter added to the API and not
#: here would be silently dropped from every view saved afterwards.
LIST_KEYS: dict[str, str] = {
    "sector": "sectors",
    "department": "departments",
    "company": "companies",
    "job_level": "job_levels",
    "program": "program_ids",
    "program_type": "program_types",
    "program_target": "program_targets",
    "trainer": "trainer_keys",
    "learner": "employee_keys",
}
#: The list keys whose values are integers rather than strings.
INT_KEYS: frozenset[str] = frozenset({"program", "trainer", "learner"})
#: A tuple, not a set: this order is the order they are written back in, and
#: a set's iteration order would make two saves of one view differ by string.
DATE_KEYS: tuple[str, ...] = ("date_from", "date_to")

#: Not a filter — a view's own parameter, kept so saving a breakdown remembers
#: which dimension it was broken down by. Mirrors `RESERVED` in `filters.ts`.
PASSTHROUGH: frozenset[str] = frozenset({"by"})


class InvalidQuery(ValueError):
    """A saved view's query string does not describe filters this API accepts."""


def parse_query(query: str) -> tuple[MetricFilters, str]:
    """Read a stored query string into filters, and rebuild it canonically.

    Returns the filters and the query string as it will be stored — rebuilt
    from what was understood rather than kept verbatim, so a saved view cannot
    carry a parameter that reached it by accident and cannot preserve a
    duplicate or an ordering that makes two identical views look different.
    """
    kept: list[tuple[str, str]] = []
    dates: dict[str, dt.date] = {}
    lists: dict[str, list[str]] = {}

    for key, value in parse_qsl(query.lstrip("?"), keep_blank_values=False):
        if key in PASSTHROUGH:
            kept.append((key, value))
            continue
        if key in DATE_KEYS:
            try:
                dates[key] = dt.date.fromisoformat(value)
            except ValueError:
                raise InvalidQuery(f"{key} is not a date: {value!r}") from None
            continue
        if key not in LIST_KEYS:
            raise InvalidQuery(f"{key} is not a filter this API accepts")
        if key in INT_KEYS and not value.lstrip("-").isdigit():
            raise InvalidQuery(f"{key} takes a number, not {value!r}")
        lists.setdefault(key, []).append(value)

    filters = MetricFilters(
        date_from=dates.get("date_from"),
        date_to=dates.get("date_to"),
        **{
            field: frozenset(int(v) for v in values) if key in INT_KEYS else frozenset(values)
            for key, field in LIST_KEYS.items()
            if (values := lists.get(key))
        },  # type: ignore[arg-type]
    )

    canonical: list[tuple[str, str]] = [
        (key, dates[key].isoformat()) for key in DATE_KEYS if key in dates
    ]
    for key in LIST_KEYS:
        canonical.extend((key, value) for value in sorted(set(lists.get(key, []))))
    canonical.extend(sorted(set(kept)))
    # Encoded, not joined by hand. `company=The MarQ Communities` written raw
    # comes back as three parameters the next time anything parses it, and a
    # saved view that decays into a different filter set is worse than one that
    # refuses to save.
    return filters, urlencode(canonical)


# --------------------------------------------------------------------- shapes
class ViewOut(BaseModel):
    id: int
    name: str
    path: str
    #: Without a leading `?`. Empty means the unfiltered view, which is a
    #: legitimate thing to save on a screen that defaults to something else.
    query: str
    #: The scope in words, resolved at save time.
    describes: str
    owner_email: str
    #: Whether the signed-in user may rename or delete this one.
    mine: bool
    created_at: dt.datetime
    updated_at: dt.datetime


class ViewsResponse(BaseModel):
    views: list[ViewOut]


class SaveRequest(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    path: Annotated[str, Field(min_length=1, max_length=200)]
    query: Annotated[str, Field(default="", max_length=4000)]

    @field_validator("name")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        # P-05 was trailing whitespace splitting one value into two. A saved
        # view is not a metric dimension, but the same two-minutes-later
        # confusion applies: "Q3" and "Q3 " sorting apart in a short list.
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("a view needs a name")
        return trimmed


class RenameRequest(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]

    @field_validator("name")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("a view needs a name")
        return trimmed


def _out(view: SavedView, viewer: str) -> ViewOut:
    return ViewOut(
        id=view.id,
        name=view.name,
        path=view.path,
        query=view.query,
        describes=view.describes,
        owner_email=view.owner_email,
        mine=view.owner_email == viewer,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


# --------------------------------------------------------------------- routes
@router.get("", response_model=ViewsResponse)
def list_views(user: CurrentUser, session: DbSession) -> ViewsResponse:
    """Everybody's saved views, the signed-in user's first."""
    views = session.scalars(
        select(SavedView).order_by(
            # Yours at the top, then alphabetically. A shared list ordered only
            # by name buries your own views among a colleague's.
            (SavedView.owner_email != user.email),
            SavedView.name,
        )
    ).all()
    return ViewsResponse(views=[_out(view, user.email) for view in views])


@router.post("", response_model=ViewOut, status_code=status.HTTP_201_CREATED)
def save_view(request: SaveRequest, user: CurrentUser, session: DbSession) -> ViewOut:
    """Save the current screen and filters under a name."""
    if request.path not in PATHS:
        raise HTTPException(status_code=422, detail=f"{request.path} is not a view of this app")
    try:
        filters, canonical = parse_query(request.query)
    except InvalidQuery as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    view = SavedView(
        owner_email=user.email,
        name=request.name,
        path=request.path,
        query=canonical,
        describes=filters.describe(),
    )
    session.add(view)
    try:
        # `get_db` commits nothing — read paths must not — so every write route
        # in this API commits for itself.
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409, detail=f"You already have a view called {request.name!r}."
        ) from None
    session.refresh(view)
    return _out(view, user.email)


def _own(session: Session, view_id: int, email: str) -> SavedView:
    view = session.get(SavedView, view_id)
    if view is None:
        raise HTTPException(status_code=404, detail="No such view.")
    if view.owner_email != email:
        # 403 rather than 404: the view is visible to this person in the list,
        # and pretending it does not exist would be a puzzle rather than an
        # answer.
        raise HTTPException(status_code=403, detail=f"That view belongs to {view.owner_email}.")
    return view


@router.patch("/{view_id}", response_model=ViewOut)
def rename_view(
    view_id: int, request: RenameRequest, user: CurrentUser, session: DbSession
) -> ViewOut:
    """Rename one of your own views."""
    view = _own(session, view_id, user.email)
    view.name = request.name
    view.updated_at = dt.datetime.now(dt.UTC)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409, detail=f"You already have a view called {request.name!r}."
        ) from None
    session.refresh(view)
    return _out(view, user.email)


# `response_class=Response` because 204 carries no body, and FastAPI derives a
# JSON one from the return annotation unless told otherwise.
@router.delete("/{view_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_view(view_id: int, user: CurrentUser, session: DbSession) -> Response:
    """Delete one of your own views.

    A hard delete, and the one place in the `app` schema where that is right.
    Everything else here supersedes because it changed a published figure and
    the prior value is owed to an auditor; a bookmark owes nobody anything.
    """
    view = _own(session, view_id, user.email)
    session.delete(view)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["LIST_KEYS", "PATHS", "InvalidQuery", "parse_query", "router"]
