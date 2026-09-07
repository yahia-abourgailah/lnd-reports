"""Breakdowns and trends: one metric, computed over several slices.

Both work the same way — narrow the filters, compute the metric again, repeat —
and that choice is worth defending because the obvious alternative is faster.

Pushing a `GROUP BY` inside each metric would answer in one query instead of
thirty. It would also mean every metric has two implementations: the headline
one and the grouped one. They would agree on the day they were written. A
population rule corrected in one and not the other is invisible — both return
plausible numbers, and the only symptom is that a breakdown stops summing to
the total it sits under, which nobody checks because it always used to.

So a slice is the same metric with a different filter, and the parts are the
whole by construction. The cost is real: thirty sectors is thirty queries per
metric, which is why `lnd.metrics.cache` exists.

`RatioMetric` is what makes the arithmetic hold. Each slice returns its own
numerator and denominator, so re-aggregating sums both terms rather than
averaging quotients — 100% over 2 responses and 80% over 98 combine to 80.4%,
not to 90%.
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lnd.metrics import registry
from lnd.metrics.base import MetricValue
from lnd.metrics.filters import Dimension, MetricFilters, UnsupportedFilter
from lnd.models.core import DimEmployee, DimProgram

#: Slices past which a breakdown stops being readable and starts being a table
#: nobody scrolls. 240 departments is a CSV export, not a chart.
MAX_SLICES = 60


@dataclass(frozen=True)
class Slice:
    """One bar of a breakdown, or one point of a trend."""

    key: str
    label: str
    value: MetricValue


@dataclass(frozen=True)
class Breakdown:
    metric_key: str
    dimension: Dimension
    #: The same metric with no slicing. Returned beside the parts so the client
    #: can show "the whole" without a second request — and so a breakdown that
    #: does not add up to it is visible rather than inferred.
    overall: MetricValue
    slices: tuple[Slice, ...]
    #: Slices dropped by MAX_SLICES. Reported rather than silently truncated.
    omitted: int


@dataclass(frozen=True)
class Trend:
    metric_key: str
    overall: MetricValue
    points: tuple[Slice, ...]


def _employee_values(session: Session, column_name: str) -> list[str]:
    column = getattr(DimEmployee, column_name)
    statement = (
        select(column)
        .where(DimEmployee.is_current, DimEmployee.on_current_roster, column.is_not(None))
        .group_by(column)
        .order_by(func.count().desc(), column)
    )
    return [str(value) for value in session.scalars(statement)]


def _program_values(session: Session, column_name: str) -> list[str]:
    column = getattr(DimProgram, column_name)
    statement = (
        select(column)
        .where(DimProgram.deleted_at_source.is_(None), column.is_not(None))
        .group_by(column)
        .order_by(func.count().desc(), column)
    )
    return [str(value) for value in session.scalars(statement)]


def _narrow(filters: MetricFilters, dimension: Dimension, value: str) -> MetricFilters:
    """The same filters, plus one dimension pinned to one value."""
    one: frozenset[str] = frozenset({value})
    if dimension is Dimension.SECTOR:
        return MetricFilters(**{**vars(filters), "sectors": one})
    if dimension is Dimension.DEPARTMENT:
        return MetricFilters(**{**vars(filters), "departments": one})
    if dimension is Dimension.COMPANY:
        return MetricFilters(**{**vars(filters), "companies": one})
    if dimension is Dimension.JOB_LEVEL:
        return MetricFilters(**{**vars(filters), "job_levels": one})
    if dimension is Dimension.PROGRAM_TYPE:
        return MetricFilters(**{**vars(filters), "program_types": one})
    if dimension is Dimension.PROGRAM_TARGET:
        return MetricFilters(**{**vars(filters), "program_targets": one})
    if dimension is Dimension.PROGRAM:
        return MetricFilters(**{**vars(filters), "program_ids": frozenset({int(value)})})
    if dimension is Dimension.TRAINER:
        return MetricFilters(**{**vars(filters), "trainer_keys": frozenset({int(value)})})
    raise UnsupportedFilter(f"{dimension} cannot be broken down by value")


_SLICE_SOURCES: dict[Dimension, tuple[str, str]] = {
    Dimension.SECTOR: ("employee", "sector"),
    Dimension.DEPARTMENT: ("employee", "department_name"),
    Dimension.COMPANY: ("employee", "company_name"),
    Dimension.JOB_LEVEL: ("employee", "job_level_name"),
    Dimension.PROGRAM_TYPE: ("program", "type"),
    Dimension.PROGRAM_TARGET: ("program", "target"),
}


def breakdown(
    metric_key: str,
    session: Session,
    dimension: Dimension,
    filters: MetricFilters | None = None,
) -> Breakdown:
    """One metric, sliced by one dimension.

    Refused rather than approximated when the metric does not define that
    dimension. A trainer breakdown of Participation Rate would narrow the
    numerator by trainer and leave the denominator whole — the arithmetic that
    produced the published 60.4% (P-13).
    """
    metric = registry.get(metric_key)
    applied = filters or MetricFilters()
    metric.spec.reject_unsupported(applied)

    if dimension not in metric.spec.supports:
        raise UnsupportedFilter(
            f"{metric_key} cannot be broken down by {dimension.value}: it counts over "
            f"{metric.spec.population.description}, which has no {dimension.value}."
        )
    if dimension is Dimension.PERIOD:
        raise UnsupportedFilter("period is a trend, not a breakdown — use /trend")

    source, column = _SLICE_SOURCES[dimension]
    values = (
        _employee_values(session, column)
        if source == "employee"
        else _program_values(session, column)
    )
    kept, omitted = values[:MAX_SLICES], max(len(values) - MAX_SLICES, 0)

    slices = tuple(
        Slice(
            key=value,
            label=value,
            value=metric.compute(session, _narrow(applied, dimension, value)),
        )
        for value in kept
    )
    return Breakdown(
        metric_key=metric_key,
        dimension=dimension,
        overall=metric.compute(session, applied),
        slices=slices,
        omitted=omitted,
    )


def _months(session: Session, filters: MetricFilters) -> list[dt.date]:
    """Every month the data spans, oldest first.

    Bounded by the data rather than by the filters, then narrowed — a trend
    that started at the first filtered row would begin at a different month for
    every filter and make two charts uncomparable.
    """
    first, last = session.execute(
        select(func.min(DimProgram.start_date), func.max(DimProgram.end_date)).where(
            DimProgram.deleted_at_source.is_(None)
        )
    ).one()
    if first is None:
        return []
    start = max(first, filters.date_from) if filters.date_from else first
    end = min(last or first, filters.date_to) if filters.date_to else (last or first)

    months: list[dt.date] = []
    cursor = start.replace(day=1)
    while cursor <= end:
        months.append(cursor)
        cursor = (
            cursor.replace(year=cursor.year + 1, month=1)
            if cursor.month == 12
            else cursor.replace(month=cursor.month + 1)
        )
    return months


def trend(metric_key: str, session: Session, filters: MetricFilters | None = None) -> Trend:
    """One metric, month by month.

    Each point is the metric computed over that month alone, which is what a
    trend of a *rate* has to mean: a running total would rise forever and say
    nothing about whether February was better than March.

    Participation Rate is the exception that proves the design — its numerator
    narrows to the month while its denominator stays the headcount at period
    end, because that asymmetry is declared inside the metric rather than here.
    """
    metric = registry.get(metric_key)
    applied = filters or MetricFilters()
    metric.spec.reject_unsupported(applied)

    if Dimension.PERIOD not in metric.spec.supports:
        raise UnsupportedFilter(f"{metric_key} has no period to trend over")

    points: list[Slice] = []
    for month in _months(session, applied):
        last_day = calendar.monthrange(month.year, month.month)[1]
        windowed = MetricFilters(
            **{
                **vars(applied),
                "date_from": month,
                "date_to": month.replace(day=last_day),
            }
        )
        points.append(
            Slice(
                key=f"{month.year:04d}-{month.month:02d}",
                label=f"{calendar.month_abbr[month.month]} {month.year}",
                value=metric.compute(session, windowed),
            )
        )

    return Trend(
        metric_key=metric_key,
        overall=metric.compute(session, applied),
        points=tuple(points),
    )


def excluded_count(session: Session) -> int:
    """Rows kept out of every metric because they could not be resolved.

    Returned on every response (FR-A05). A dashboard that silently omits rows
    is the workbook; one that says "and 3 we could not place" is a platform.
    """
    from lnd.models.ops import DqException, DqStatus

    return int(
        session.scalar(
            select(func.count()).select_from(DqException).where(DqException.status == DqStatus.OPEN)
        )
        or 0
    )


__all__ = ["Breakdown", "Slice", "Trend", "breakdown", "excluded_count", "trend"]
