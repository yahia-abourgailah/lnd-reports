"""Coverage: participation by dimension, and who has had nothing.

`/v1/coverage` is the breakdown; `/v1/coverage/untrained` is the list of names,
and it answers with counts rather than names until somebody has narrowed to a
department, sector, company or job level. That gate is a judgement about how a
list of 1,268 people who have done nothing reads in a meeting, not a technical
limit — `analysis.coverage` says so at length, and it is one predicate to lift.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from lnd.analysis import coverage as service
from lnd.api.v1.kpis import Envelope, FilterParams, MetricOut, _envelope, _out
from lnd.auth.dependencies import CurrentUser
from lnd.db import get_db
from lnd.metrics.filters import Dimension, UnsupportedFilter

router = APIRouter(prefix="/coverage", tags=["coverage"])

DbSession = Annotated[Session, Depends(get_db)]


class TailOut(BaseModel):
    """What the breakdown could not show, in slices and in people.

    129 departments against a slice limit of 60. Named rather than truncated:
    a chart that quietly stops at 60 is a chart whose bottom is invisible.
    """

    dimension: str
    values_total: int
    values_shown: int
    values_omitted: int
    employees_omitted: int


class CoverageSliceOut(BaseModel):
    key: str
    label: str
    participation: MetricOut
    untrained: MetricOut


class CoverageResponse(Envelope):
    dimension: str
    overall_participation: MetricOut
    overall_untrained: MetricOut
    months_since_last_training: MetricOut
    slices: list[CoverageSliceOut]
    tail: TailOut


class UntrainedResponse(Envelope):
    total: int
    #: True when nothing has been narrowed, and the names are withheld.
    gated: bool
    #: What to narrow by to see them.
    gate_note: str
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool


GATE_NOTE = (
    "Names are shown once the view is narrowed to a department, sector, company or "
    "job level. The count is exact either way — it is the roster of individuals that "
    "is scoped, not the figure."
)

COLUMNS = ["employee_code", "name", "company", "department", "sector", "job_level"]


@router.get("", response_model=CoverageResponse)
def coverage(
    _user: CurrentUser,
    session: DbSession,
    filters: FilterParams,
    by: Annotated[Dimension, Query(description="the dimension to slice by")] = Dimension.SECTOR,
) -> CoverageResponse:
    if by not in service.COVERAGE_DIMENSIONS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"coverage cannot be sliced by {by.value}: participation is a property of "
                "people, and this dimension does not describe one."
            ),
        )
    try:
        result = service.coverage(session, by, filters)
    except UnsupportedFilter as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    return CoverageResponse(
        dimension=result.dimension.value,
        overall_participation=_out(result.overall_participation),
        overall_untrained=_out(result.overall_untrained),
        months_since_last_training=_out(result.months_since_last_training),
        slices=[
            CoverageSliceOut(
                key=s.key,
                label=s.label,
                participation=_out(s.participation),
                untrained=_out(s.untrained),
            )
            for s in result.slices
        ],
        tail=TailOut(**{**vars(result.tail), "dimension": result.tail.dimension.value}),
        **_envelope(session, filters, was_cached=False),
    )


@router.get("/untrained", response_model=UntrainedResponse)
def untrained(_user: CurrentUser, session: DbSession, filters: FilterParams) -> UntrainedResponse:
    try:
        result = service.untrained(session, filters)
    except UnsupportedFilter as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    return UntrainedResponse(
        total=result.total,
        gated=result.gated,
        gate_note=GATE_NOTE,
        columns=COLUMNS,
        rows=list(result.rows),
        truncated=result.truncated,
        **_envelope(session, filters, was_cached=False),
    )
