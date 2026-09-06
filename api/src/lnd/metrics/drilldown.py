"""The rows behind a number.

Every metric can be opened. Click 55 and see the 55 programs; click 13.9% and
see the 201 people who attended and the 1,450 who could have.

This is not a convenience feature. The workbook's numbers were unauditable
because they lived inside GETPIVOTDATA strings — you could read the result and
you could not read the question, so a figure that looked wrong could only be
argued about. A metric that cannot be opened is a metric nobody can check, and
six of the nine turned out to need checking.

The grain is taken from the metric's own `Population`, so the rows returned are
by construction the rows the number was computed over. A separate hand-written
drill-through query per metric would be a second definition of the population,
free to drift from the first — which is the failure this whole layer exists to
prevent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from lnd.metrics import registry, scope
from lnd.metrics.filters import Dimension, MetricFilters
from lnd.metrics.population import Grain
from lnd.models.core import (
    DimEmployee,
    DimProgram,
    DimSession,
    DimTrainer,
    FactAttendance,
    FactEnrollment,
    FactEvaluation,
)

#: How many rows a drill-through returns before it insists on being narrowed.
#: A screen cannot show 1,450 rows usefully and a browser should not be asked to
#: hold them; `total` always reports the true count so the cap is visible rather
#: than silently truncating.
DEFAULT_LIMIT = 200
MAX_LIMIT = 2000


@dataclass(frozen=True)
class Drilldown:
    """The rows behind one metric, and how many there really are."""

    metric_key: str
    grain: Grain
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    total: int
    #: True when `total` exceeds what was returned. The caller must say so on
    #: screen — a truncated list that looks complete is worse than no list.
    truncated: bool
    filters_applied: str


def _program_rows(filters: MetricFilters) -> Select[Any]:
    """The programs a program-grain metric counted.

    One scoped subquery, used once. Building it twice — once as a FROM and once
    inside the IN — produced a cartesian product that reported 3,025 programs
    for a metric whose answer is 55, which is 55 squared and exactly the kind of
    plausible-looking wrong number this layer exists to make impossible.
    """
    scoped = scope.programs(filters).subquery()
    return (
        select(
            DimProgram.crm_program_id.label("program_id"),
            DimProgram.title,
            DimProgram.computed_status.label("status"),
            DimProgram.type,
            DimProgram.target,
            DimProgram.capacity,
            DimProgram.start_date,
            DimProgram.end_date,
        )
        .where(DimProgram.crm_program_id.in_(select(scoped.c.crm_program_id)))
        .order_by(DimProgram.start_date.desc().nulls_last(), DimProgram.title)
    )


def _session_rows(filters: MetricFilters) -> Select[Any]:
    scoped = scope.sessions(filters).subquery()
    return (
        select(
            DimSession.crm_session_id.label("session_id"),
            DimProgram.title.label("program"),
            DimSession.session_date,
            DimSession.duration_hours,
            DimSession.duration_derivable,
            DimTrainer.canonical_name.label("trainer"),
            DimSession.location_name.label("location"),
        )
        .join(DimProgram, DimSession.crm_program_id == DimProgram.crm_program_id)
        .join(DimTrainer, DimSession.trainer_key == DimTrainer.trainer_key, isouter=True)
        .where(DimSession.crm_session_id.in_(select(scoped.c.crm_session_id)))
        .order_by(DimSession.session_date.desc().nulls_last())
    )


def _attendance_rows(filters: MetricFilters) -> Select[Any]:
    scoped = scope.attendances(filters).subquery()
    return (
        select(
            DimEmployee.employee_code,
            DimEmployee.full_name.label("name"),
            DimEmployee.department_name.label("department"),
            DimProgram.title.label("program"),
            FactAttendance.attended_date,
            FactAttendance.learning_hours,
        )
        .join(DimEmployee, FactAttendance.employee_key == DimEmployee.employee_key)
        .join(DimProgram, FactAttendance.crm_program_id == DimProgram.crm_program_id)
        .where(
            DimEmployee.is_current,
            FactAttendance.attendance_key.in_(select(scoped.c.attendance_key)),
        )
        .order_by(FactAttendance.attended_date.desc().nulls_last(), DimEmployee.full_name)
    )


def _enrollment_rows(filters: MetricFilters) -> Select[Any]:
    scoped = scope.enrollments(filters).subquery()
    return (
        select(
            DimEmployee.employee_code,
            DimEmployee.full_name.label("name"),
            DimEmployee.department_name.label("department"),
            DimProgram.title.label("program"),
            FactEnrollment.enrolled_date,
        )
        .join(DimEmployee, FactEnrollment.employee_key == DimEmployee.employee_key)
        .join(DimProgram, FactEnrollment.crm_program_id == DimProgram.crm_program_id)
        .where(
            DimEmployee.is_current,
            FactEnrollment.enrollment_key.in_(select(scoped.c.enrollment_key)),
        )
        .order_by(FactEnrollment.enrolled_date.desc().nulls_last(), DimEmployee.full_name)
    )


def _evaluation_rows(filters: MetricFilters) -> Select[Any]:
    """Responses, with the comment.

    The comment is included on purpose. A quality score of 91.9% is a number
    somebody will want to explain, and the 88 free-text answers are the only
    place the explanation exists.
    """
    scoped = scope.evaluations(filters).subquery()
    return (
        select(
            DimEmployee.employee_code,
            DimEmployee.full_name.label("name"),
            DimProgram.title.label("program"),
            FactEvaluation.responded_date,
            FactEvaluation.score_knowledge_relevance.label("knowledge"),
            FactEvaluation.score_activity_effectiveness.label("activity"),
            FactEvaluation.score_logistics_effectiveness.label("logistics"),
            FactEvaluation.score_facilitator_performance.label("facilitator"),
            FactEvaluation.recommend_score.label("recommend"),
            FactEvaluation.nps_band,
            FactEvaluation.comment,
        )
        .join(DimEmployee, FactEvaluation.employee_key == DimEmployee.employee_key)
        .join(DimProgram, FactEvaluation.crm_program_id == DimProgram.crm_program_id)
        .where(
            DimEmployee.is_current,
            FactEvaluation.evaluation_key.in_(select(scoped.c.evaluation_key)),
        )
        .order_by(FactEvaluation.responded_date.desc().nulls_last())
    )


def _employee_rows(filters: MetricFilters) -> Select[Any]:
    """The denominator, as a list of people.

    This is what makes Participation Rate arguable rather than assertable. The
    workbook's 192 could only be disputed; a list of 1,450 names can be checked
    against a payroll report by somebody who knows the company.
    """
    scoped = scope.enrollable_employees(filters.without(Dimension.PERIOD)).subquery()
    return (
        select(
            DimEmployee.employee_code,
            DimEmployee.full_name.label("name"),
            DimEmployee.company_name.label("company"),
            DimEmployee.department_name.label("department"),
            DimEmployee.sector,
            DimEmployee.job_level_name.label("job_level"),
        )
        .where(DimEmployee.employee_key.in_(select(scoped.c.employee_key)))
        .order_by(DimEmployee.full_name)
    )


_BY_GRAIN = {
    Grain.PROGRAM: _program_rows,
    Grain.SESSION: _session_rows,
    Grain.ATTENDANCE: _attendance_rows,
    Grain.ENROLLMENT: _enrollment_rows,
    Grain.EVALUATION: _evaluation_rows,
    Grain.EMPLOYEE: _employee_rows,
}


def rows_behind(
    metric_key: str,
    session: Session,
    filters: MetricFilters | None = None,
    *,
    limit: int = DEFAULT_LIMIT,
) -> Drilldown:
    """The rows one metric was computed over.

    The filters are the metric's own — passed through unchanged, and validated
    by the same `reject_unsupported` the metric applies. A drill-through that
    accepted a filter the metric refused would show rows the number was not
    computed over, which is a worse lie than showing none.
    """
    metric = registry.get(metric_key)
    applied = filters or MetricFilters()
    metric.spec.reject_unsupported(applied)

    grain = metric.spec.population.grain
    statement = _BY_GRAIN[grain](applied)

    total = int(session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    capped = max(1, min(limit, MAX_LIMIT))
    result = session.execute(statement.limit(capped))

    rows = tuple(dict(row) for row in result.mappings())
    return Drilldown(
        metric_key=metric_key,
        grain=grain,
        columns=tuple(statement.selected_columns.keys()),
        rows=rows,
        total=total,
        truncated=total > len(rows),
        filters_applied=applied.describe(),
    )
