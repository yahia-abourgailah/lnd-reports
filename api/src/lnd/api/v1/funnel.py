"""Enrolled → attended → evaluated, and the rows behind each stage."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from lnd.analysis import funnel as service
from lnd.api.v1.kpis import Envelope, FilterParams, MetricOut, _envelope, _out
from lnd.auth.dependencies import CurrentUser
from lnd.db import get_db

router = APIRouter(prefix="/funnel", tags=["funnel"])

DbSession = Annotated[Session, Depends(get_db)]


class StepOut(BaseModel):
    stage: str
    label: str
    count: int
    #: The metric that names the drop out of this stage. The drop is that
    #: metric's own numerator, never `this count minus the next` — a walk-in
    #: cancels a no-show out of the difference.
    drop_metric: MetricOut | None = None
    drop_count: int | None = None
    drop_label: str | None = None


class FunnelResponse(Envelope):
    steps: list[StepOut]
    no_show_rate: MetricOut
    survey_response_rate: MetricOut
    walk_ins: int
    filters_applied_funnel: str


class StageRowsResponse(Envelope):
    stage: str
    columns: list[str]
    rows: list[dict[str, Any]]
    total: int
    truncated: bool


@router.get("", response_model=FunnelResponse)
def funnel(_user: CurrentUser, session: DbSession, filters: FilterParams) -> FunnelResponse:
    result = service.funnel(session, filters)
    return FunnelResponse(
        steps=[
            StepOut(
                stage=step.stage.value,
                label=step.label,
                count=step.count,
                drop_metric=_out(step.drop_metric) if step.drop_metric else None,
                drop_count=step.drop_count,
                drop_label=step.drop_label,
            )
            for step in result.steps
        ],
        no_show_rate=_out(result.no_show_rate),
        survey_response_rate=_out(result.survey_response_rate),
        walk_ins=result.walk_ins,
        filters_applied_funnel=result.filters_applied,
        **_envelope(session, filters, was_cached=False),
    )


@router.get("/{stage}", response_model=StageRowsResponse)
def stage_rows(
    stage: service.Stage,
    _user: CurrentUser,
    session: DbSession,
    filters: FilterParams,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> StageRowsResponse:
    """The people behind one stage, at the stage's own grain.

    One person on one programme — the same statement the count came from, so a
    stage that reads 650 opens to 650 rows rather than to the 1,165 attendance
    rows underneath them.
    """
    result = service.stage_rows(session, stage, filters, limit=limit)
    return StageRowsResponse(
        stage=result.stage.value,
        columns=list(result.columns),
        rows=list(result.rows),
        total=result.total,
        truncated=result.truncated,
        **_envelope(session, filters, was_cached=False),
    )
