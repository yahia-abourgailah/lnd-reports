"""Enrolled → attended → evaluated, at one grain.

ONE GRAIN, AND THIS IS THE VIEW WHERE THAT IS EASIEST TO GET WRONG

Enrollments are per programme. Attendances are per *session*. A person at three
sessions of one programme is one attendee, not three, so a funnel built by
counting rows would show more people attending than enrolled and call it a
93% turnout. Every stage here is one person on one programme, and the
attendance stage is a distinct pair rather than a row count — which is the same
correction Survey Response Rate needed when it read 103.1%.

THE DROPS ARE METRICS, NOT SUBTRACTIONS

`enrolled - attended` looks like the number of no-shows and is not. A walk-in
attends without enrolling, so each one cancels a genuine no-show out of the
difference: a programme half the enrolled list skipped can report a drop of
zero. The no-show count comes from No-show Rate, which matches on the person
*and* the programme, and the walk-ins are reported separately instead of being
allowed to net off silently.

So the four movements between three stages are: no-shows out, walk-ins in, then
responses in and silence out. They are stated rather than implied, and the
arithmetic that ties them together is asserted in `test_funnel`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from lnd.metrics import registry, scope
from lnd.metrics.base import MetricValue
from lnd.metrics.filters import MetricFilters
from lnd.models.core import DimEmployee, DimProgram


class Stage(StrEnum):
    """The three stages, and the two groups that move between them."""

    ENROLLED = "enrolled"
    ATTENDED = "attended"
    EVALUATED = "evaluated"
    #: Enrolled, never attended.
    NO_SHOW = "no_show"
    #: Attended without ever enrolling.
    WALK_IN = "walk_in"


@dataclass(frozen=True)
class Step:
    """One stage, and the movement into the next one."""

    stage: Stage
    label: str
    count: int
    #: The metric that names the drop out of this stage, where one does.
    drop_metric: MetricValue | None = None
    drop_count: int | None = None
    drop_label: str | None = None


@dataclass(frozen=True)
class Funnel:
    steps: tuple[Step, ...]
    no_show_rate: MetricValue
    survey_response_rate: MetricValue
    #: Attendances with no enrollment. Small today and never zero by
    #: construction; reported because the difference between stages does not
    #: account for them.
    walk_ins: int
    filters_applied: str


def _attended_pairs(filters: MetricFilters) -> Select[Any]:
    """One person on one programme, from attendance at any of its sessions.

    Rows with no employee key are excluded here as everywhere else — they are
    quarantined, not dropped, and they are counted by their own metric. Leaving
    them in would make the count of a stage larger than the list of people
    behind it, which is the one thing a funnel must not do.
    """
    attended = scope.attendances(filters).subquery()
    return (
        select(attended.c.employee_key, attended.c.crm_program_id)
        .where(attended.c.employee_key.is_not(None))
        .distinct()
    )


def _enrolled_pairs(filters: MetricFilters) -> Select[Any]:
    enrolled = scope.enrollments(filters).subquery()
    return (
        select(enrolled.c.employee_key, enrolled.c.crm_program_id)
        .where(enrolled.c.employee_key.is_not(None))
        .distinct()
    )


def _no_show_pairs(filters: MetricFilters) -> Select[Any]:
    enrolled = _enrolled_pairs(filters).subquery()
    attended = _attended_pairs(filters).subquery()
    return select(enrolled.c.employee_key, enrolled.c.crm_program_id).where(
        ~select(1)
        .select_from(attended)
        .where(
            attended.c.employee_key == enrolled.c.employee_key,
            attended.c.crm_program_id == enrolled.c.crm_program_id,
        )
        .exists()
    )


def _walk_in_pairs(filters: MetricFilters) -> Select[Any]:
    enrolled = _enrolled_pairs(filters).subquery()
    attended = _attended_pairs(filters).subquery()
    return select(attended.c.employee_key, attended.c.crm_program_id).where(
        ~select(1)
        .select_from(enrolled)
        .where(
            enrolled.c.employee_key == attended.c.employee_key,
            enrolled.c.crm_program_id == attended.c.crm_program_id,
        )
        .exists()
    )


def _evaluated_pairs(filters: MetricFilters) -> Select[Any]:
    evaluated = scope.evaluations(filters).subquery()
    return (
        select(evaluated.c.employee_key, evaluated.c.crm_program_id)
        .where(evaluated.c.employee_key.is_not(None))
        .distinct()
    )


_PAIRS = {
    Stage.ENROLLED: _enrolled_pairs,
    Stage.ATTENDED: _attended_pairs,
    Stage.EVALUATED: _evaluated_pairs,
    Stage.NO_SHOW: _no_show_pairs,
    Stage.WALK_IN: _walk_in_pairs,
}


def funnel(session: Session, filters: MetricFilters | None = None) -> Funnel:
    """Three numbers and two drops. Resisting a fourth is part of the design."""
    applied = filters or MetricFilters()

    no_show_rate = registry.compute("no_show_rate", session, applied)
    response_rate = registry.compute("survey_response_rate", session, applied)

    enrolled = scope.count_of(session, _enrolled_pairs(applied))
    attended = scope.count_of(session, _attended_pairs(applied))
    evaluated = scope.count_of(session, _evaluated_pairs(applied))
    no_shows = scope.count_of(session, _no_show_pairs(applied))
    walk_ins = scope.count_of(session, _walk_in_pairs(applied))

    def percent(part: int, whole: int) -> str:
        if whole == 0:
            return "—"
        return f"{Decimal(part) / Decimal(whole) * 100:.1f}%"

    steps = (
        Step(
            stage=Stage.ENROLLED,
            label="Enrolled",
            count=enrolled,
            drop_metric=no_show_rate,
            drop_count=no_shows,
            drop_label=(
                f"{no_shows:,} enrolled and never attended — {percent(no_shows, enrolled)} no-show"
            ),
        ),
        Step(
            stage=Stage.ATTENDED,
            label="Attended",
            count=attended,
            drop_metric=response_rate,
            drop_count=attended - evaluated,
            drop_label=(
                f"{attended - evaluated:,} never responded — "
                f"{percent(evaluated, attended)} response rate"
            ),
        ),
        Step(stage=Stage.EVALUATED, label="Evaluated", count=evaluated),
    )

    return Funnel(
        steps=steps,
        no_show_rate=no_show_rate,
        survey_response_rate=response_rate,
        walk_ins=walk_ins,
        filters_applied=applied.describe(),
    )


@dataclass(frozen=True)
class StageRows:
    stage: Stage
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    total: int
    truncated: bool
    filters_applied: str


def stage_rows(
    session: Session, stage: Stage, filters: MetricFilters | None = None, *, limit: int = 500
) -> StageRows:
    """The people behind one stage, at the stage's own grain.

    Built from the same pair statements the counts come from, so a stage that
    reads 650 opens to 650 rows. A separate query per stage would be a second
    definition of what "attended" means, and the two would agree until the day
    one of them was corrected.
    """
    applied = filters or MetricFilters()
    pairs = _PAIRS[stage](applied).subquery()

    listed = (
        select(
            DimEmployee.employee_code,
            DimEmployee.full_name.label("name"),
            DimEmployee.department_name.label("department"),
            DimProgram.title.label("program"),
        )
        .select_from(pairs)
        .join(DimEmployee, DimEmployee.employee_key == pairs.c.employee_key)
        .join(DimProgram, DimProgram.crm_program_id == pairs.c.crm_program_id)
        .order_by(DimEmployee.full_name, DimProgram.title)
    )

    total = int(session.scalar(select(func.count()).select_from(pairs)) or 0)
    rows = tuple(dict(row) for row in session.execute(listed.limit(limit)).mappings())
    return StageRows(
        stage=stage,
        columns=tuple(listed.selected_columns.keys()),
        rows=rows,
        total=total,
        truncated=total > len(rows),
        filters_applied=applied.describe(),
    )


__all__ = ["Funnel", "Stage", "StageRows", "Step", "funnel", "stage_rows"]
