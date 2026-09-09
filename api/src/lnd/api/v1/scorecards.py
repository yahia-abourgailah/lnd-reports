"""Programme and trainer scorecards.

Four routes: a list and a scorecard for each. Both scorecards carry the same
envelope every other read endpoint does — freshness, the filters applied, what
is excluded — because a scorecard read on its own is exactly where a stale or
narrowed figure would go unnoticed.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from lnd.analysis import scorecards
from lnd.api.v1.kpis import Envelope, FilterParams, MetricOut, _envelope, _out
from lnd.auth.dependencies import CurrentUser
from lnd.db import get_db

router = APIRouter(tags=["scorecards"])

DbSession = Annotated[Session, Depends(get_db)]


class ProgramSummary(BaseModel):
    crm_program_id: int
    title: str
    type: str | None = None
    target: str | None = None
    capacity: int | None = None
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    #: Distinct people who attended. Defaulted so `ProgramHeaderOut`, which
    #: describes one programme rather than a list, is unaffected.
    participants: int = 0
    #: Rows, not people — the enrollment grain is one person on one programme,
    #: and the gap between this and `participants` is what the overview's
    #: enrolment chart draws.
    enrollments: int = 0


class ProgramHeaderOut(ProgramSummary):
    status: str | None = None
    customised_department_name: str | None = None
    #: Every trainer who delivered a session of it. More than one is common and
    #: is why the programme-level trainer is often unset.
    trainer_names: list[str]


class SessionOut(BaseModel):
    crm_session_id: int
    session_date: dt.date
    duration_hours: Decimal | None
    #: False where the times would not subtract. The session is still listed —
    #: it happened — and is excluded from both hour metrics with an exception
    #: raised, which is what "counted or excepted, never neither" means here.
    duration_derivable: bool
    trainer: str | None
    location: str | None
    attendees: int


class CommentOut(BaseModel):
    """A free-text answer, whole and unattributed. See `analysis.scorecards`."""

    program_title: str
    responded_date: dt.date | None
    recommend_score: int | None
    nps_band: str | None
    text: str


class ProgramScorecardResponse(Envelope):
    program: ProgramHeaderOut
    figures: list[MetricOut]
    sessions: list[SessionOut]
    comments: list[CommentOut]
    filters_applied_scorecard: str


class TrainerSummary(BaseModel):
    trainer_key: int
    canonical_name: str
    is_placeholder: bool
    is_external: bool
    sessions: int
    programs: int
    attendances: int


class TrainerHeaderOut(BaseModel):
    trainer_key: int
    canonical_name: str
    is_placeholder: bool
    is_external: bool


class ContributionOut(BaseModel):
    crm_program_id: int
    title: str
    metric: MetricOut


class TrainerScorecardResponse(Envelope):
    trainer: TrainerHeaderOut
    #: Narrowed by trainer: what they delivered.
    delivery: list[MetricOut]
    #: Narrowed to the programmes they delivered. A weaker claim, and the true
    #: one: a survey response belongs to a programme, not to a session, so on a
    #: shared programme both trainers carry the same responses.
    programme_level: list[MetricOut]
    nps_by_program: list[ContributionOut]
    program_ids: list[int]
    filters_applied_scorecard: str


class ProgramListResponse(BaseModel):
    programs: list[ProgramSummary]


class TrainerListResponse(BaseModel):
    trainers: list[TrainerSummary]


@router.get("/programs", response_model=ProgramListResponse)
def programs(_user: CurrentUser, session: DbSession, filters: FilterParams) -> ProgramListResponse:
    rows: list[dict[str, Any]] = scorecards.program_index(session, filters)
    return ProgramListResponse(programs=[ProgramSummary(**row) for row in rows])


@router.get("/programs/{program_id}/scorecard", response_model=ProgramScorecardResponse)
def program_scorecard(
    program_id: int, _user: CurrentUser, session: DbSession, filters: FilterParams
) -> ProgramScorecardResponse:
    try:
        card = scorecards.program_scorecard(session, program_id, filters)
    except scorecards.UnknownProgram as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None

    return ProgramScorecardResponse(
        program=ProgramHeaderOut(
            **{**vars(card.program), "trainer_names": list(card.program.trainer_names)}
        ),
        figures=[_out(figure) for figure in card.figures],
        sessions=[SessionOut(**vars(row)) for row in card.sessions],
        comments=[CommentOut(**vars(comment)) for comment in card.comments],
        filters_applied_scorecard=card.filters_applied,
        **_envelope(session, filters, was_cached=False),
    )


@router.get("/trainers", response_model=TrainerListResponse)
def trainers(_user: CurrentUser, session: DbSession, filters: FilterParams) -> TrainerListResponse:
    rows: list[dict[str, Any]] = scorecards.trainer_index(session, filters)
    return TrainerListResponse(trainers=[TrainerSummary(**row) for row in rows])


@router.get("/trainers/{trainer_key}/scorecard", response_model=TrainerScorecardResponse)
def trainer_scorecard(
    trainer_key: int, _user: CurrentUser, session: DbSession, filters: FilterParams
) -> TrainerScorecardResponse:
    try:
        card = scorecards.trainer_scorecard(session, trainer_key, filters)
    except scorecards.UnknownTrainer as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None

    return TrainerScorecardResponse(
        trainer=TrainerHeaderOut(**vars(card.trainer)),
        delivery=[_out(figure) for figure in card.delivery],
        programme_level=[_out(figure) for figure in card.programme_level],
        nps_by_program=[
            ContributionOut(crm_program_id=c.crm_program_id, title=c.title, metric=_out(c.value))
            for c in card.nps_by_program
        ],
        program_ids=list(card.program_ids),
        filters_applied_scorecard=card.filters_applied,
        **_envelope(session, filters, was_cached=False),
    )
