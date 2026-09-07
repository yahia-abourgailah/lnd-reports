"""What can be filtered on, and what values exist.

The filter bar reads this rather than hardcoding a list. Three reasons, and the
third is the one that matters:

Values change. Sectors, departments and job levels come from the CRM's user
object and are whatever the CRM currently says. A hardcoded list goes stale
silently — a new department appears in the data and cannot be selected.

Only values that exist are offered. Filtering to a department nobody is in
returns an empty dashboard that looks like a bug. The counts returned here let
the bar say "Sales (312)" so the shape of the answer is visible before anybody
asks for it.

And a dimension nobody can filter on is a dimension the API should not accept.
The same vocabulary drives the bar, the breakdown endpoint and the metric
`supports` sets, so the three cannot drift into disagreeing about what a filter
is.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from lnd.metrics.filters import Dimension
from lnd.models.core import DimEmployee, DimProgram, DimTrainer


@dataclass(frozen=True)
class DimensionValue:
    """One selectable value, and how many rows carry it."""

    value: str
    label: str
    count: int


@dataclass(frozen=True)
class DimensionOptions:
    dimension: Dimension
    label: str
    #: What one row of `count` is, so the bar can say "312 employees" rather
    #: than a bare number that could mean anything.
    counts: str
    values: tuple[DimensionValue, ...]


#: Employee attributes are read from the version current now, matching how every
#: metric filters. Reading them as-of would offer a department that existed in
#: March and does not now, which is a filter that returns nothing.
_EMPLOYEE_ATTRIBUTES: dict[Dimension, tuple[str, str]] = {
    Dimension.SECTOR: ("sector", "Sector"),
    Dimension.DEPARTMENT: ("department_name", "Department"),
    Dimension.COMPANY: ("company_name", "Company"),
    Dimension.JOB_LEVEL: ("job_level_name", "Job level"),
}

_PROGRAM_ATTRIBUTES: dict[Dimension, tuple[str, str]] = {
    Dimension.PROGRAM_TYPE: ("type", "Delivered by"),
    Dimension.PROGRAM_TARGET: ("target", "Audience"),
}


def _values(session: Session, statement: Select[tuple[str, int]]) -> tuple[DimensionValue, ...]:
    return tuple(
        DimensionValue(value=str(value), label=str(value), count=int(count))
        for value, count in session.execute(statement).all()
        if value is not None
    )


def _employee_options(session: Session, dimension: Dimension) -> DimensionOptions:
    column_name, label = _EMPLOYEE_ATTRIBUTES[dimension]
    column = getattr(DimEmployee, column_name)
    statement = (
        select(column, func.count())
        .where(DimEmployee.is_current, DimEmployee.on_current_roster, column.is_not(None))
        .group_by(column)
        .order_by(func.count().desc(), column)
    )
    return DimensionOptions(
        dimension=dimension, label=label, counts="employees", values=_values(session, statement)
    )


def _program_options(session: Session, dimension: Dimension) -> DimensionOptions:
    column_name, label = _PROGRAM_ATTRIBUTES[dimension]
    column = getattr(DimProgram, column_name)
    statement = (
        select(column, func.count())
        .where(DimProgram.deleted_at_source.is_(None), column.is_not(None))
        .group_by(column)
        .order_by(func.count().desc(), column)
    )
    return DimensionOptions(
        dimension=dimension, label=label, counts="programs", values=_values(session, statement)
    )


def _program_list(session: Session) -> DimensionOptions:
    """Programs, by title but keyed on id.

    The value is the CRM id and never the title. Two programs have shared a
    title before and were merged into one row by a report that grouped on it
    (P-02); a filter bar that sent titles would reintroduce exactly that.
    """
    statement = (
        select(DimProgram.crm_program_id, DimProgram.title)
        .where(DimProgram.deleted_at_source.is_(None))
        .order_by(DimProgram.start_date.desc().nulls_last(), DimProgram.title)
    )
    return DimensionOptions(
        dimension=Dimension.PROGRAM,
        label="Program",
        counts="programs",
        values=tuple(
            DimensionValue(value=str(program_id), label=title, count=1)
            for program_id, title in session.execute(statement).all()
        ),
    )


def _trainer_list(session: Session) -> DimensionOptions:
    """Trainers, excluding neither the vendor nor the placeholder.

    `Belton Academy` is external and `L&D Team` is not a person, but both
    delivered sessions somebody may want to look at. They are offered and
    labelled rather than hidden — a filter bar that silently omitted them would
    make their sessions unreachable.
    """
    statement = select(DimTrainer.trainer_key, DimTrainer.canonical_name).order_by(
        DimTrainer.canonical_name
    )
    return DimensionOptions(
        dimension=Dimension.TRAINER,
        label="Trainer",
        counts="trainers",
        values=tuple(
            DimensionValue(value=str(key), label=name, count=1)
            for key, name in session.execute(statement).all()
        ),
    )


def available(session: Session) -> tuple[DimensionOptions, ...]:
    """Every dimension the filter bar may offer, with its current values.

    `Dimension.PERIOD` is absent on purpose: a date range is not a list of
    values to pick from, and offering every month as a checkbox would be a
    worse date picker than a date picker.
    """
    return (
        *(_employee_options(session, d) for d in _EMPLOYEE_ATTRIBUTES),
        *(_program_options(session, d) for d in _PROGRAM_ATTRIBUTES),
        _program_list(session),
        _trainer_list(session),
    )
