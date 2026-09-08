"""Turning a population plus filters into a query.

One place, so every metric narrows the same way. Three rules hold for all of
them and are applied here rather than remembered twenty-one times:

**Soft-deleted rows never count.** Every fact carries `deleted_at_source`, set
by the nightly reconcile when the CRM stops returning a record. A cancelled
program has to leave the numerator, and a metric that forgot would keep
reporting it indefinitely with nothing to show the difference.

**Unresolved identities never count.** A row whose person could not be
identified is quarantined, not dropped — it stays queryable and stays out of
every metric. Counting it would attribute somebody's attendance to nobody;
dropping it would hide that anybody had.

**A learner filter narrows the facts, never the roster.** `employee_keys` is
applied to attendance, enrollment and evaluation — the three grains that record
what a person did. It is deliberately absent from `enrollable_employees`: a
participation rate for one person is 1/1 or 0/1, which is not a rate, and a
metric that accepted the filter on one side only would be P-13 again at the
grain of an individual. The metrics that count over a roster refuse the
dimension instead.

**Employee attributes come from the version current now.** Sector, department
and job level are read from `is_current`, which is a decision rather than an
oversight: "attendance by department" means today's departments, so a
reorganisation restates the breakdown rather than splitting one team across two
names. As-of attribution is available through `dim_employee`'s validity window
when a metric wants it, and Participation Rate is the one that does.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.orm import Session

from lnd.metrics.filters import Dimension, MetricFilters
from lnd.models.core import (
    DimEmployee,
    DimProgram,
    DimSession,
    FactAttendance,
    FactEnrollment,
    FactEvaluation,
    IdentityStatus,
)

#: Any fact table's row that the nightly reconcile has retired.
LIVE_ONLY = "deleted_at_source IS NULL"


def _employee_predicates(filters: MetricFilters) -> list[ColumnElement[bool]]:
    """Sector, department, company and job level, from the current version."""
    where: list[ColumnElement[bool]] = []
    if filters.sectors:
        where.append(DimEmployee.sector.in_(sorted(filters.sectors)))
    if filters.departments:
        where.append(DimEmployee.department_name.in_(sorted(filters.departments)))
    if filters.companies:
        where.append(DimEmployee.company_name.in_(sorted(filters.companies)))
    if filters.job_levels:
        where.append(DimEmployee.job_level_name.in_(sorted(filters.job_levels)))
    return where


def _program_predicates(filters: MetricFilters) -> list[ColumnElement[bool]]:
    where: list[ColumnElement[bool]] = []
    if filters.program_ids:
        where.append(DimProgram.crm_program_id.in_(sorted(filters.program_ids)))
    if filters.program_types:
        where.append(DimProgram.type.in_(sorted(filters.program_types)))
    if filters.program_targets:
        where.append(DimProgram.target.in_(sorted(filters.program_targets)))
    return where


def _needs_employee(filters: MetricFilters) -> bool:
    return bool(filters.sectors or filters.departments or filters.companies or filters.job_levels)


def _needs_program(filters: MetricFilters) -> bool:
    return bool(filters.program_ids or filters.program_types or filters.program_targets)


def attendances(filters: MetricFilters) -> Select[Any]:
    """Live, identified attendance rows in scope."""
    statement = select(FactAttendance).where(
        FactAttendance.deleted_at_source.is_(None),
        FactAttendance.identity_status != IdentityStatus.UNRESOLVED,
    )
    if filters.employee_keys:
        statement = statement.where(FactAttendance.employee_key.in_(sorted(filters.employee_keys)))
    if filters.date_from is not None:
        statement = statement.where(FactAttendance.attended_date >= filters.date_from)
    if filters.date_to is not None:
        statement = statement.where(FactAttendance.attended_date <= filters.date_to)
    if _needs_employee(filters):
        statement = statement.join(
            DimEmployee, FactAttendance.employee_key == DimEmployee.employee_key
        ).where(DimEmployee.is_current, *_employee_predicates(filters))
    if _needs_program(filters) or filters.trainer_keys:
        statement = statement.join(
            DimProgram, FactAttendance.crm_program_id == DimProgram.crm_program_id
        ).where(*_program_predicates(filters))
    if filters.trainer_keys:
        statement = statement.join(
            DimSession, FactAttendance.crm_session_id == DimSession.crm_session_id
        ).where(DimSession.trainer_key.in_(sorted(filters.trainer_keys)))
    return statement


def enrollments(filters: MetricFilters) -> Select[Any]:
    statement = select(FactEnrollment).where(
        FactEnrollment.deleted_at_source.is_(None),
        FactEnrollment.identity_status != IdentityStatus.UNRESOLVED,
    )
    if filters.employee_keys:
        statement = statement.where(FactEnrollment.employee_key.in_(sorted(filters.employee_keys)))
    if filters.date_from is not None:
        statement = statement.where(FactEnrollment.enrolled_date >= filters.date_from)
    if filters.date_to is not None:
        statement = statement.where(FactEnrollment.enrolled_date <= filters.date_to)
    if _needs_employee(filters):
        statement = statement.join(
            DimEmployee, FactEnrollment.employee_key == DimEmployee.employee_key
        ).where(DimEmployee.is_current, *_employee_predicates(filters))
    if _needs_program(filters):
        statement = statement.join(
            DimProgram, FactEnrollment.crm_program_id == DimProgram.crm_program_id
        ).where(*_program_predicates(filters))
    return statement


def evaluations(filters: MetricFilters) -> Select[Any]:
    statement = select(FactEvaluation).where(
        FactEvaluation.deleted_at_source.is_(None),
        FactEvaluation.identity_status != IdentityStatus.UNRESOLVED,
    )
    if filters.employee_keys:
        statement = statement.where(FactEvaluation.employee_key.in_(sorted(filters.employee_keys)))
    if filters.date_from is not None:
        statement = statement.where(FactEvaluation.responded_date >= filters.date_from)
    if filters.date_to is not None:
        statement = statement.where(FactEvaluation.responded_date <= filters.date_to)
    if _needs_employee(filters):
        statement = statement.join(
            DimEmployee, FactEvaluation.employee_key == DimEmployee.employee_key
        ).where(DimEmployee.is_current, *_employee_predicates(filters))
    if _needs_program(filters):
        statement = statement.join(
            DimProgram, FactEvaluation.crm_program_id == DimProgram.crm_program_id
        ).where(*_program_predicates(filters))
    return statement


def sessions(filters: MetricFilters, *, completed_only: bool = True) -> Select[Any]:
    """Sessions, joined to their program so status and type can narrow them."""
    statement = (
        select(DimSession)
        .join(DimProgram, DimSession.crm_program_id == DimProgram.crm_program_id)
        .where(DimSession.deleted_at_source.is_(None), DimProgram.deleted_at_source.is_(None))
    )
    if completed_only:
        # computed_status, never status. They disagree on 55 of 57 programs.
        statement = statement.where(DimProgram.computed_status == "completed")
    if filters.date_from is not None:
        statement = statement.where(DimSession.session_date >= filters.date_from)
    if filters.date_to is not None:
        statement = statement.where(DimSession.session_date <= filters.date_to)
    if filters.trainer_keys:
        statement = statement.where(DimSession.trainer_key.in_(sorted(filters.trainer_keys)))
    return statement.where(*_program_predicates(filters))


def programs(filters: MetricFilters, *, completed_only: bool = True) -> Select[Any]:
    """Programs in scope.

    The period narrows on `start_date`, which is when a program ran rather than
    when it was created — the question "how many programs in February" is about
    delivery, and created_at would answer a different one.
    """
    statement = select(DimProgram).where(DimProgram.deleted_at_source.is_(None))
    if completed_only:
        statement = statement.where(DimProgram.computed_status == "completed")
    if filters.date_from is not None:
        statement = statement.where(
            or_(
                DimProgram.start_date.is_(None),
                DimProgram.start_date >= filters.date_from,
            )
        )
    if filters.date_to is not None:
        statement = statement.where(
            or_(DimProgram.start_date.is_(None), DimProgram.start_date <= filters.date_to)
        )
    if filters.trainer_keys:
        statement = statement.where(DimProgram.trainer_key.in_(sorted(filters.trainer_keys)))
    return statement.where(*_program_predicates(filters))


def enrollable_employees(filters: MetricFilters) -> Select[Any]:
    """The participation denominator.

    Three exclusions, each of which was a published error:

      * `on_current_roster` — the 149 people who trained and have since left,
        plus two companies that no longer exist. They stay in the dimension so
        their attendance keys; they are not staff who could attend today.
      * `status = 'active'` — leavers the roster still lists.
      * no date filter is applied to the count itself. Participation is
        attendance *within a period* over headcount *at period end*, and
        narrowing the denominator by the same dates would count only people who
        happened to attend — which is how a rate of 100% gets published.
    """
    as_of = filters.date_to
    statement = select(DimEmployee).where(
        DimEmployee.on_current_roster,
        DimEmployee.status == "active",
    )
    if as_of is None:
        statement = statement.where(DimEmployee.is_current)
    else:
        statement = statement.where(
            DimEmployee.valid_from <= as_of,
            or_(DimEmployee.valid_to.is_(None), DimEmployee.valid_to > as_of),
        )
    return statement.where(*_employee_predicates(filters))


def untrained_employees(filters: MetricFilters) -> Select[Any]:
    """The Coverage Gap, as rows: enrollable people with no attendance in scope.

    One expression, called by both the metric that counts these people and the
    view that lists them. Two would be two definitions of "untrained", and the
    symptom of their drifting apart is a list whose length is not the number
    printed above it — visible to a reader, and unfalsifiable by them.

    The asymmetry is the same one Participation Rate declares: eligibility is
    as of the period end, attendance is within the period. Narrowing
    eligibility by the same dates would leave only people who attended, and the
    gap would be empty by construction.
    """
    eligible = enrollable_employees(filters.without(Dimension.PERIOD)).subquery()
    attended = attendances(filters).subquery()
    return (
        select(DimEmployee)
        .where(
            DimEmployee.employee_key.in_(select(eligible.c.employee_key)),
            DimEmployee.employee_key.not_in(select(attended.c.employee_key)),
        )
        .order_by(DimEmployee.full_name)
    )


def count_of(session: Session, statement: Select[Any]) -> int:
    """How many rows the statement selects.

    Through a subquery rather than by swapping the select list, so a statement
    that already joined and filtered is counted exactly as written. Rewriting it
    would be one more place for the counted population to drift from the
    reported one.
    """
    return int(session.scalar(select(func.count()).select_from(statement.subquery())) or 0)


__all__ = [
    "attendances",
    "count_of",
    "enrollable_employees",
    "enrollments",
    "evaluations",
    "programs",
    "sessions",
    "untrained_employees",
]
