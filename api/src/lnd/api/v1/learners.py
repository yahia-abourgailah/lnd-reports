"""Learner profiles and the top-learners ranking.

Three routes. `/v1/learners/top` returns the shape of the ranking always and
the names only once the view is narrowed — the same rule `/v1/coverage/untrained`
follows, for the same reason read from the other end. `/v1/learners/search`
is the way into a profile that is not a ranking, and it requires something to
search for.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from lnd.analysis import learners as service
from lnd.api.v1.kpis import Envelope, FilterParams, MetricOut, _envelope, _out
from lnd.auth.dependencies import CurrentUser
from lnd.db import get_db

router = APIRouter(prefix="/learners", tags=["learners"])

DbSession = Annotated[Session, Depends(get_db)]

GATE_NOTE = (
    "Names are shown once the view is narrowed to a department, sector, company or "
    "job level. The counts and the spread of hours are exact either way — it is the "
    "ranked list of individuals that is scoped, not the figures."
)


class RankOut(BaseModel):
    #: Competition-style: equal hours share a rank and the next rank skips.
    rank: int
    employee_key: int
    employee_code: str | None
    name: str | None
    department: str | None
    company: str | None
    #: Shown beside hours because the three measures disagree — more sessions
    #: is not more programmes, and neither is more hours.
    programs: int
    sessions: int
    hours: Decimal


class TopLearnersResponse(Envelope):
    total_learners: int
    gated: bool
    gate_note: str
    rows: list[RankOut]
    hours_max: Decimal | None
    hours_median: Decimal | None
    hours_min: Decimal | None
    truncated: bool
    filters_applied_ranking: str


class AttendedProgramOut(BaseModel):
    crm_program_id: int
    title: str
    sessions: int
    hours: Decimal | None
    first_attended: dt.date | None
    last_attended: dt.date | None


class LearnerProfileResponse(Envelope):
    employee_key: int
    employee_code: str | None
    name: str | None
    department: str | None
    company: str | None
    sector: str | None
    job_level: str | None
    position: str | None
    #: False for somebody who trained and has since left. Their history is real
    #: and stays visible; they are simply not in any headcount denominator.
    on_current_roster: bool
    figures: list[MetricOut]
    programs: list[AttendedProgramOut]
    filters_applied_profile: str


class SearchResponse(BaseModel):
    query: str
    results: list[dict[str, Any]]


@router.get("/top", response_model=TopLearnersResponse)
def top_learners(
    _user: CurrentUser,
    session: DbSession,
    filters: FilterParams,
    limit: Annotated[int, Query(ge=1, le=service.MAX_LIMIT)] = service.DEFAULT_LIMIT,
) -> TopLearnersResponse:
    """The ranking, derived rather than typed (P-09, P-10).

    Before `/{employee_key}` in the route table on purpose — `top` would
    otherwise be read as a key and fail to parse as an integer.
    """
    result = service.top_learners(session, filters, limit=limit)
    return TopLearnersResponse(
        total_learners=result.total_learners,
        gated=result.gated,
        gate_note=GATE_NOTE,
        rows=[RankOut(**vars(row)) for row in result.rows],
        hours_max=result.hours_max,
        hours_median=result.hours_median,
        hours_min=result.hours_min,
        truncated=result.truncated,
        filters_applied_ranking=result.filters_applied,
        **_envelope(session, filters, was_cached=False),
    )


@router.get("/search", response_model=SearchResponse)
def search(
    _user: CurrentUser,
    session: DbSession,
    q: Annotated[str, Query(description="a name or employee code")] = "",
) -> SearchResponse:
    return SearchResponse(query=q, results=service.find(session, q))


@router.get("/{employee_key}", response_model=LearnerProfileResponse)
def profile(
    employee_key: int, _user: CurrentUser, session: DbSession, filters: FilterParams
) -> LearnerProfileResponse:
    try:
        person = service.profile(session, employee_key, filters)
    except service.UnknownLearner as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None

    return LearnerProfileResponse(
        employee_key=person.employee_key,
        employee_code=person.employee_code,
        name=person.name,
        department=person.department,
        company=person.company,
        sector=person.sector,
        job_level=person.job_level,
        position=person.position,
        on_current_roster=person.on_current_roster,
        figures=[_out(figure) for figure in person.figures],
        programs=[AttendedProgramOut(**vars(row)) for row in person.programs],
        filters_applied_profile=person.filters_applied,
        **_envelope(session, filters, was_cached=False),
    )
