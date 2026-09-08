"""Who has been reached, and who has not.

The lopsided one. Measured on live data, 1,268 of 1,469 active employees have
never attended anything, and coverage runs from 6.4% at one company to 64.1% at
another. That gap is the finding of the week and it is not a data defect: both
numerators and both denominators come from the same roster, which is precisely
what P-13 fixed.

None of this is derivable from attendance. A person who has never attended
appears in no programme payload at all, which is why the platform needed a
roster before this screen could exist and why the workbook could never have
produced it.

THE TAIL

129 departments against a `MAX_SLICES` of 60. The breakdown reports what it
omitted rather than truncating quietly, and this module turns that count into a
sentence a reader can act on: how many departments are not shown, and how many
employees are in them. The alternative — an "other" bar — needs the remainder
computed somewhere other than the metric, which is a second population.

THE NAMES

`untrained` is gated. A count by department is planning; a company-wide
sortable list of 1,268 people who have done nothing is a different document in
a different meeting, and the difference is not technical. So the names are
returned only once somebody has narrowed to a department, sector, company or
job level, and the ungated call returns counts. The gate is one predicate and
is trivially removable if L&D decide otherwise — deliberately, because that is
their decision and not ours.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lnd.metrics import aggregate, registry, scope
from lnd.metrics.base import MetricValue
from lnd.metrics.filters import Dimension, MetricFilters
from lnd.models.core import DimEmployee

#: What coverage may be broken down by. Sector and job level fit on a screen at
#: 27 and 20 values; company at 5; department is here too and needs the tail
#: sentence below.
COVERAGE_DIMENSIONS = (
    Dimension.COMPANY,
    Dimension.SECTOR,
    Dimension.JOB_LEVEL,
    Dimension.DEPARTMENT,
)

#: A narrowing that makes the named list a scoped question rather than a roster.
#: Period is not among them: "everyone untrained since January" is still
#: everyone.
GATING_DIMENSIONS = (
    Dimension.DEPARTMENT,
    Dimension.SECTOR,
    Dimension.COMPANY,
    Dimension.JOB_LEVEL,
)


@dataclass(frozen=True)
class Tail:
    """The slices a breakdown could not show.

    Reported in employees as well as in slices, because "69 departments not
    shown" and "69 departments holding 118 people" support different decisions.
    """

    dimension: Dimension
    values_total: int
    values_shown: int
    values_omitted: int
    employees_omitted: int


@dataclass(frozen=True)
class CoverageSlice:
    key: str
    label: str
    participation: MetricValue
    untrained: MetricValue


@dataclass(frozen=True)
class Coverage:
    dimension: Dimension
    overall_participation: MetricValue
    overall_untrained: MetricValue
    months_since_last_training: MetricValue
    slices: tuple[CoverageSlice, ...]
    tail: Tail
    filters_applied: str


@dataclass(frozen=True)
class UntrainedList:
    """Either the names, or the reason they are not being shown.

    `gated` is true when nothing has been narrowed. The count is returned either
    way — the number is not the sensitive part, the list is.
    """

    total: int
    gated: bool
    rows: tuple[dict[str, Any], ...]
    truncated: bool
    filters_applied: str


def _tail(session: Session, dimension: Dimension, omitted: int, shown: int) -> Tail:
    """How much of a dimension the breakdown left out, in slices and in people."""
    source, column = aggregate.SLICE_SOURCES[dimension]
    values = (
        aggregate.employee_values(session, column)
        if source == "employee"
        else aggregate.program_values(session, column)
    )
    employees = 0
    if omitted and source == "employee":
        attribute = getattr(DimEmployee, column)
        employees = int(
            session.scalar(
                select(func.count())
                .select_from(DimEmployee)
                .where(
                    DimEmployee.is_current,
                    DimEmployee.on_current_roster,
                    DimEmployee.status == "active",
                    attribute.in_(values[shown:]),
                )
            )
            or 0
        )
    return Tail(
        dimension=dimension,
        values_total=len(values),
        values_shown=shown,
        values_omitted=omitted,
        employees_omitted=employees,
    )


def coverage(
    session: Session, dimension: Dimension, filters: MetricFilters | None = None
) -> Coverage:
    """Participation and the gap, sliced one way.

    Both metrics are re-run under each slice's filters rather than grouped
    inside a query, so the parts are the whole by construction: the slices' own
    numerators and denominators sum to the overall figure, which is a test
    rather than a convention.
    """
    applied = filters or MetricFilters()
    participation = aggregate.breakdown("participation_rate", session, dimension, applied)
    gap = aggregate.breakdown("coverage_gap", session, dimension, applied)
    gap_by_key = {s.key: s.value for s in gap.slices}

    slices = tuple(
        CoverageSlice(
            key=s.key,
            label=s.label,
            participation=s.value,
            untrained=gap_by_key[s.key],
        )
        for s in participation.slices
        if s.key in gap_by_key
    )

    return Coverage(
        dimension=dimension,
        overall_participation=participation.overall,
        overall_untrained=gap.overall,
        months_since_last_training=registry.compute("months_since_last_training", session, applied),
        slices=slices,
        tail=_tail(session, dimension, participation.omitted, len(participation.slices)),
        filters_applied=applied.describe(),
    )


def is_gated(filters: MetricFilters) -> bool:
    """True where nothing narrows the list to a scope somebody chose."""
    return not (filters.dimensions_used & set(GATING_DIMENSIONS))


def untrained(
    session: Session, filters: MetricFilters | None = None, *, limit: int = 2000
) -> UntrainedList:
    """The people with no attendance in scope — by name, once a scope is chosen.

    The count comes from the same statement as the list and the same statement
    as the Coverage Gap metric, so the length of what is shown and the number
    printed above it cannot disagree.
    """
    applied = filters or MetricFilters()
    statement = scope.untrained_employees(applied)
    total = scope.count_of(session, statement)

    if is_gated(applied):
        return UntrainedList(
            total=total,
            gated=True,
            rows=(),
            truncated=False,
            filters_applied=applied.describe(),
        )

    rows = session.execute(
        select(
            DimEmployee.employee_code,
            DimEmployee.full_name.label("name"),
            DimEmployee.company_name.label("company"),
            DimEmployee.department_name.label("department"),
            DimEmployee.sector,
            DimEmployee.job_level_name.label("job_level"),
        )
        .where(DimEmployee.employee_key.in_(select(statement.subquery().c.employee_key)))
        .order_by(DimEmployee.full_name)
        .limit(limit)
    ).mappings()
    materialised = tuple(dict(row) for row in rows)

    return UntrainedList(
        total=total,
        gated=False,
        rows=materialised,
        truncated=total > len(materialised),
        filters_applied=applied.describe(),
    )


__all__ = [
    "COVERAGE_DIMENSIONS",
    "GATING_DIMENSIONS",
    "Coverage",
    "CoverageSlice",
    "Tail",
    "UntrainedList",
    "coverage",
    "is_gated",
    "untrained",
]
