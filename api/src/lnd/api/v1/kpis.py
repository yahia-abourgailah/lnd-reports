"""The dashboard's read endpoints.

Four routes, one filter shape, one envelope. Every response carries three
things beyond the numbers, and each answers a question somebody would otherwise
have to guess at:

    freshness       how old is this? A figure with no age is a figure nobody
                    can tell is stale, and the platform serves last-known-good
                    on purpose when a source is down (NFR-03).
    filters         what was this computed over? P-03 was a filter applied
                    somewhere up a spreadsheet and inherited by everything
                    below it, with nothing on screen to say so.
    excluded_count  how many rows could not be placed? A dashboard that
                    silently omits rows is the workbook.

Filters arrive as repeated query parameters — `?sector=Finance&sector=Sales` —
because that is what a filter bar produces and what a URL can hold. The whole
bar round-trips through the query string, so a link to a filtered view is just
a link.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from lnd.auth.dependencies import CurrentUser
from lnd.db import get_db
from lnd.metrics import aggregate, cache, dimensions, registry
from lnd.metrics.base import MetricValue
from lnd.metrics.filters import Dimension, MetricFilters, UnsupportedFilter
from lnd.sync.freshness import FreshnessResponse, platform_freshness

router = APIRouter(prefix="/kpis", tags=["kpis"])

DbSession = Annotated[Session, Depends(get_db)]


# ---------------------------------------------------------------- the envelope
class MetricOut(BaseModel):
    """One figure, with everything needed to read it honestly."""

    key: str
    title: str
    #: Shown in the tooltip and stamped on every export. The number and its
    #: definition ship together or the definition is not enforceable.
    definition: str
    population: str
    #: What this metric excludes, so "why is it lower than I expected" has an
    #: answer on screen rather than in a docstring.
    excludes: list[str]
    provenance: str
    #: Present for anything restated, renamed or corrected — the sentence to
    #: read out when somebody asks why it changed.
    note: str | None = None
    unit: str

    value: Decimal | None
    formatted: str
    numerator: Decimal | None = None
    denominator: Decimal | None = None
    #: How many rows the value rests on. A quality score over 297 responses and
    #: one over 4 are different claims.
    sample_size: int
    is_estimated: bool


class Envelope(BaseModel):
    freshness: FreshnessResponse
    filters_applied: str
    dimensions_filtered: list[str]
    #: Rows a data-quality rule keeps out of the figures.
    excluded_count: int
    #: Rows a rule flagged and still counted. Reported separately because
    #: calling them excluded understates the platform's coverage — and says
    #: something untrue in the direction that sounds careful.
    flagged_count: int
    cached: bool


class KpisResponse(Envelope):
    metrics: list[MetricOut]


class SliceOut(BaseModel):
    key: str
    label: str
    metric: MetricOut


class BreakdownResponse(Envelope):
    metric_key: str
    dimension: str
    overall: MetricOut
    slices: list[SliceOut]
    #: Slices beyond the readable limit. Named rather than silently dropped.
    omitted: int


class TrendResponse(Envelope):
    metric_key: str
    overall: MetricOut
    points: list[SliceOut]


class DimensionValueOut(BaseModel):
    value: str
    label: str
    count: int


class DimensionOut(BaseModel):
    dimension: str
    label: str
    counts: str
    values: list[DimensionValueOut]


class DimensionsResponse(BaseModel):
    dimensions: list[DimensionOut]


# ---------------------------------------------------------------- conversion
def _out(value: MetricValue) -> MetricOut:
    spec = registry.get(value.key).spec
    return MetricOut(
        key=value.key,
        title=value.title,
        definition=value.definition,
        population=value.population.description,
        excludes=list(value.population.excludes),
        provenance=value.provenance.value,
        note=spec.note or None,
        unit=value.unit.value,
        value=value.value,
        formatted=value.formatted(),
        numerator=value.numerator,
        denominator=value.denominator,
        sample_size=value.sample_size,
        is_estimated=value.is_estimated,
    )


#: Repeated query parameters, all optional. Annotated rather than bare so
#: FastAPI reads them as `?sector=A&sector=B` with a default of "not filtered",
#: instead of treating every one as required.
Repeated = Query(default_factory=list)


def _filters(
    date_from: Annotated[dt.date | None, Query()] = None,
    date_to: Annotated[dt.date | None, Query()] = None,
    sector: Annotated[list[str], Repeated] = [],  # noqa: B006 - FastAPI reads the annotation
    department: Annotated[list[str], Repeated] = [],  # noqa: B006
    company: Annotated[list[str], Repeated] = [],  # noqa: B006
    job_level: Annotated[list[str], Repeated] = [],  # noqa: B006
    program: Annotated[list[int], Repeated] = [],  # noqa: B006
    program_type: Annotated[list[str], Repeated] = [],  # noqa: B006
    program_target: Annotated[list[str], Repeated] = [],  # noqa: B006
    trainer: Annotated[list[int], Repeated] = [],  # noqa: B006
    # Accepted here, and deliberately not offered by the filter bar. A learner
    # filter is how a profile page asks for one person's figures; as a global
    # control it would turn every screen into a search for an individual, which
    # is a different product from the one L&D asked for.
    learner: Annotated[list[int], Repeated] = [],  # noqa: B006
) -> MetricFilters:
    return MetricFilters(
        date_from=date_from,
        date_to=date_to,
        sectors=frozenset(sector),
        departments=frozenset(department),
        companies=frozenset(company),
        job_levels=frozenset(job_level),
        program_ids=frozenset(program),
        program_types=frozenset(program_type),
        program_targets=frozenset(program_target),
        trainer_keys=frozenset(trainer),
        employee_keys=frozenset(learner),
    )


FilterParams = Annotated[MetricFilters, Depends(_filters)]


def _envelope(session: Session, filters: MetricFilters, *, was_cached: bool) -> dict[str, Any]:
    quality = aggregate.completeness(session)
    return {
        "freshness": platform_freshness(session),
        "filters_applied": filters.describe(),
        "dimensions_filtered": sorted(d.value for d in filters.dimensions_used),
        "excluded_count": quality.excluded,
        "flagged_count": quality.flagged,
        "cached": was_cached,
    }


# -------------------------------------------------------------------- routes
@router.get("", response_model=KpisResponse)
def kpis(_user: CurrentUser, session: DbSession, filters: FilterParams) -> KpisResponse:
    """Every metric the filters allow.

    A metric that refuses the filters is absent rather than an error. Asked for
    one trainer, the dashboard shows the figures that mean something for a
    trainer; Participation Rate is not among them, and its absence is the
    honest answer to a question that has none.
    """
    key = cache.key_for("kpis", filters=filters)
    values, was_cached = cache.cached(
        key,
        lambda: list(registry.compute_all(session, filters)),
        serialise=lambda vs: [v.key for v in vs],
        # Only the keys are cached, not the numbers. Recomputing from a key
        # list is nearly all of the work, so this caches almost nothing — and
        # that is deliberate for now: see `breakdown`, where the saving is
        # real. Caching MetricValue would mean serialising Population and
        # Provenance, and a stale copy of a *definition* is worse than a slow
        # request.
        deserialise=lambda keys: [registry.compute(k, session, filters) for k in keys],
    )
    return KpisResponse(
        metrics=[_out(v) for v in values], **_envelope(session, filters, was_cached=was_cached)
    )


@router.get("/dimensions", response_model=DimensionsResponse)
def available_dimensions(_user: CurrentUser, session: DbSession) -> DimensionsResponse:
    """What the filter bar may offer, and how many rows each value has.

    Before `/kpis/{key}` in the route table on purpose — `dimensions` would
    otherwise be read as a metric key.
    """
    return DimensionsResponse(
        dimensions=[
            DimensionOut(
                dimension=option.dimension.value,
                label=option.label,
                counts=option.counts,
                values=[
                    DimensionValueOut(value=v.value, label=v.label, count=v.count)
                    for v in option.values
                ],
            )
            for option in dimensions.available(session)
        ]
    )


@router.get("/{key}/breakdown", response_model=BreakdownResponse)
def metric_breakdown(
    key: str,
    session: DbSession,
    _user: CurrentUser,
    filters: FilterParams,
    by: Annotated[Dimension, Query(description="the dimension to slice by")],
) -> BreakdownResponse:
    """One metric, sliced by one dimension.

    Refused with 422 when the metric has no such population — a trainer
    breakdown of Participation Rate would narrow the numerator and leave the
    denominator whole, which is the arithmetic behind the published 60.4%.
    """
    try:
        result = aggregate.breakdown(key, session, by, filters)
    except registry.UnknownMetric as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except UnsupportedFilter as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    return BreakdownResponse(
        metric_key=result.metric_key,
        dimension=result.dimension.value,
        overall=_out(result.overall),
        slices=[SliceOut(key=s.key, label=s.label, metric=_out(s.value)) for s in result.slices],
        omitted=result.omitted,
        **_envelope(session, filters, was_cached=False),
    )


@router.get("/{key}/trend", response_model=TrendResponse)
def metric_trend(
    key: str, session: DbSession, _user: CurrentUser, filters: FilterParams
) -> TrendResponse:
    """One metric, month by month.

    Each point is that month alone, not a running total — a cumulative line
    rises forever and says nothing about whether March was better than
    February.
    """
    try:
        result = aggregate.trend(key, session, filters)
    except registry.UnknownMetric as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except UnsupportedFilter as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    return TrendResponse(
        metric_key=result.metric_key,
        overall=_out(result.overall),
        points=[SliceOut(key=p.key, label=p.label, metric=_out(p.value)) for p in result.points],
        **_envelope(session, filters, was_cached=False),
    )
