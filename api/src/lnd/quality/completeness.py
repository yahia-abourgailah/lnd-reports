"""How much of a period is actually in the figures (FR-F04).

Every screen already says how many records are excluded *now*. This answers the
harder question a monthly report has to answer about itself: for the period this
report covers, what was left out, and by which rule.

WHY THE PERIOD IS DERIVED RATHER THAN STORED

An exception could carry the date the transform saw it. It does not, because
that date would freeze: a programme whose start date is corrected in the CRM
next week would keep the completeness figure it had under the wrong date, and
two answers to "when did this run" is exactly the drift the star schema exists
to prevent.

So the period comes from `dim_program`, through the `crm_program_id` every
exception carries, using **the same predicate `scope.programs` uses** — narrowed
on `start_date`, with a null start kept in scope rather than dropped. A
completeness figure scoped differently from the figures it describes would be
answering about a different set of programmes than the report it sits in.

WHAT AN EXCEPTION WITH NO PROGRAMME IN `core` MEANS

It is counted in the unscoped total and in no period. That is deliberate and it
is the honest reading: a violation the transform raised against a programme that
never reached `core` — soft-deleted at source, or not yet complete — has no
period to belong to, and inventing one would put it in a month it did not
happen in. `unplaceable` reports how many there are rather than letting them
vanish between the two views.
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from lnd.metrics import scope
from lnd.metrics.filters import MetricFilters
from lnd.models.core import DimProgram
from lnd.models.ops import DqDisposition, DqException, DqRule, DqStatus
from lnd.quality.catalogue import RULES


@dataclass(frozen=True)
class RuleCount:
    """One rule's contribution to the queue, in the scope asked about."""

    rule: DqRule
    title: str
    disposition: DqDisposition
    count: int
    #: True when this rule's records are missing from figures rather than
    #: merely flagged in them. The distinction the exclusion banner got wrong.
    costs_numbers: bool


@dataclass(frozen=True)
class Completeness:
    """The open queue, split by what it did to the numbers."""

    #: Records a rule keeps out of the figures it affects.
    excluded: int
    #: Records flagged and still counted in full. Reported separately because
    #: calling them excluded understates coverage — wrong in the direction that
    #: sounds careful.
    flagged: int
    by_rule: tuple[RuleCount, ...] = ()
    #: Open exceptions whose programme is not in `core`, so they belong to no
    #: period. Counted in the unscoped total; stated rather than dropped.
    unplaceable: int = 0
    filters_applied: str = "no filters — the full population"

    @property
    def total(self) -> int:
        return self.excluded + self.flagged


@dataclass(frozen=True)
class PeriodCompleteness:
    """One month's exclusions, for the trend an operator reads."""

    period: str
    excluded: int
    flagged: int


@dataclass(frozen=True)
class CompletenessTrend:
    months: tuple[PeriodCompleteness, ...] = field(default_factory=tuple)


def _scoped(filters: MetricFilters | None) -> tuple[bool, list[ColumnElement[bool]]]:
    """Predicates restricting the queue to the programmes in scope.

    Returns `(scoped, predicates)`. Unscoped means every open exception counts,
    which is what the dashboard banner asks for; scoped means only those whose
    programme is in the filtered population, which is what a period report asks.
    """
    if filters is None or filters.is_empty:
        return False, []
    in_scope = select(DimProgram.crm_program_id).where(
        DimProgram.crm_program_id.in_(
            select(scope.programs(filters, completed_only=False).subquery().c.crm_program_id)
        )
    )
    return True, [DqException.crm_program_id.in_(in_scope)]


def completeness(session: Session, filters: MetricFilters | None = None) -> Completeness:
    """The open queue in one number per disposition, plus the rules behind it."""
    scoped, predicates = _scoped(filters)

    rows = session.execute(
        select(DqException.rule, DqException.disposition, func.count())
        .where(DqException.status == DqStatus.OPEN, *predicates)
        .group_by(DqException.rule, DqException.disposition)
    ).all()

    by_rule = tuple(
        sorted(
            (
                RuleCount(
                    rule=rule,
                    title=RULES[rule].title,
                    disposition=disposition,
                    count=int(count),
                    costs_numbers=disposition is DqDisposition.QUARANTINED,
                )
                for rule, disposition, count in rows
            ),
            # Losses first, then by size. An operator reading a queue should
            # meet the rows that cost figures before the ones that do not.
            key=lambda entry: (not entry.costs_numbers, -entry.count, entry.rule.value),
        )
    )

    unplaceable = 0
    if not scoped:
        unplaceable = int(
            session.scalar(
                select(func.count())
                .select_from(DqException)
                .where(
                    DqException.status == DqStatus.OPEN,
                    DqException.crm_program_id.not_in(select(DimProgram.crm_program_id)),
                )
            )
            or 0
        )

    return Completeness(
        excluded=sum(entry.count for entry in by_rule if entry.costs_numbers),
        flagged=sum(entry.count for entry in by_rule if not entry.costs_numbers),
        by_rule=by_rule,
        unplaceable=unplaceable,
        filters_applied=(filters or MetricFilters()).describe(),
    )


def _month_window(year: int, month: int) -> MetricFilters:
    last = calendar.monthrange(year, month)[1]
    return MetricFilters(date_from=dt.date(year, month, 1), date_to=dt.date(year, month, last))


def months_in_scope(
    session: Session, filters: MetricFilters | None = None
) -> list[tuple[int, int]]:
    """The months the data actually covers, oldest first.

    Read from `dim_program` rather than from a range somebody typed. A trend
    over months with no programmes would draw a run of zeroes that look like a
    collapse in delivery rather than an absence of it.
    """
    statement = select(
        func.extract("year", DimProgram.start_date), func.extract("month", DimProgram.start_date)
    ).where(DimProgram.start_date.is_not(None), DimProgram.deleted_at_source.is_(None))
    if filters is not None:
        if filters.date_from is not None:
            statement = statement.where(DimProgram.start_date >= filters.date_from)
        if filters.date_to is not None:
            statement = statement.where(DimProgram.start_date <= filters.date_to)
    rows = session.execute(statement.distinct()).all()
    return sorted((int(year), int(month)) for year, month in rows)


def trend(session: Session, filters: MetricFilters | None = None) -> CompletenessTrend:
    """Completeness month by month — the indicator FR-F04 asks for.

    One query per month rather than one grouped query, for the same reason
    breakdowns re-run their metric: the per-month figure is the *same* function
    under a narrower window, so it cannot drift from the headline. A grouped
    second implementation would agree the day it was written.
    """
    months = []
    for year, month in months_in_scope(session, filters):
        window = _month_window(year, month)
        scoped = completeness(session, window)
        months.append(
            PeriodCompleteness(
                period=f"{year:04d}-{month:02d}",
                excluded=scoped.excluded,
                flagged=scoped.flagged,
            )
        )
    return CompletenessTrend(months=tuple(months))


__all__ = [
    "Completeness",
    "CompletenessTrend",
    "PeriodCompleteness",
    "RuleCount",
    "completeness",
    "months_in_scope",
    "trend",
]
