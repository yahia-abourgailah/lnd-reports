"""One learner's record, and the ranking across all of them.

P-09 AND P-10, WHICH ARE THE SAME SHEET TWICE

The workbook's Top Learner tab was hand-typed. Its headline formula `=B95`
pointed at a count cell rather than a name, so the published "top learner" was a
number, and a second cell referenced a workbook that is not present anywhere in
the file (P-10). Both defects have one cause: the ranking was maintained by a
person rather than derived. This module derives it from `fact_attendance`.

TIES ARE REAL, AND ORDERING BY HOURS ALONE IS NOT DETERMINISTIC

Four groups tie on hours in the live data — three people on 135.0, three on
126.0, two on 89.0, two on 80.0. PostgreSQL is under no obligation to return
equal rows in the same order twice, so `ORDER BY hours DESC` alone makes the
"top ten" reshuffle between two runs of the same query, with nothing to show
that the underlying data did not change. That is the same class of defect as a
figure that moves for no reason, and it is worse here because the rows are
people: somebody drops off a recognition list because of a plan change.

So the sort is `(hours DESC, employee_key ASC)`. The tiebreak is arbitrary and
it is *stable*, which is the property that matters. Ranks are then assigned
competition-style — equal hours share a rank and the next rank skips — so the
display never claims one of three people on 135.0 hours beat the other two.

WHY HOURS, AND WHY THE OTHER TWO COLUMNS ARE THERE ANYWAY

Ranked on Learner Hours, which is what the delivery plan names. It is not the
only defensible measure and the data says so plainly: Hany attended more
sessions than Gehad and fewer programmes. So sessions and programmes are shown
beside hours rather than hidden, because a reader who can see the three
orderings disagree will not mistake one of them for the truth.

THE NAMES WERE SCOPED, AND ARE NOT ANY MORE

This ranking was gated at first: names appeared only once the view was narrowed
to a department, sector, company or job level, following the rule week 7 set for
the zero-training list. That was a judgement made here rather than a decision
L&D asked for, and the week-8 handover recorded it as provisional.

It was the wrong call, and the requirement says so plainly — "provide a
top-learners ranking by learning hours, derived automatically, replacing the
hand-typed sheet". The sheet being replaced was a company-wide Top Learner that
the workbook already published every cycle, everyone who can sign in is L&D, and
a screen headed "Top learners" that shows no learners is not a careful version
of the feature. It is the feature withheld.

The caution is still true — a ranked list of colleagues by training hours reads
as recognition in one meeting and as a record in another — so it is said on the
screen instead of enforced by hiding rows. The three orderings are shown side by
side for the same reason: sessions, programmes and hours disagree, and a reader
who can see that will not mistake one of them for a verdict.

The zero-training list on `/coverage` is still gated. It is not the same
artefact: a named list of people who have had *nothing* is a document about
individuals in a way a ranking of who attended most is not, and nobody has asked
for that one to be opened.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from lnd.metrics import registry, scope
from lnd.metrics.base import MetricValue
from lnd.metrics.filters import Dimension, MetricFilters, UnsupportedFilter
from lnd.models.core import DimEmployee, DimProgram, FactAttendance

#: What a profile shows. Every one of these narrows both its terms to the
#: person; the metrics that count over a roster are absent because one person's
#: participation rate is 1/1, which is not a rate. `_figures` skips anything
#: that refuses the filter rather than presenting a number computed over
#: somebody else.
PROFILE_FIGURES = (
    "learner_hours",
    "no_show_rate",
    "months_since_last_training",
    "nps",
    "knowledge_relevance",
    "activity_effectiveness",
    "logistics_effectiveness",
    "facilitator_performance",
    "survey_response_rate",
)

#: How many rows a ranking returns before it insists on being narrowed.
DEFAULT_LIMIT = 50
MAX_LIMIT = 500


@dataclass(frozen=True)
class LearnerRank:
    """One row of the ranking.

    `rank` is competition-style: three people on 135.0 hours are all rank 1 and
    the next is rank 4. Sequence numbering would order them 1, 2, 3 on an
    arbitrary tiebreak and publish a difference the data does not contain.
    """

    rank: int
    employee_key: int
    employee_code: str | None
    name: str | None
    department: str | None
    company: str | None
    programs: int
    sessions: int
    hours: Decimal


@dataclass(frozen=True)
class TopLearners:
    """The ranking, or the shape of it without the names."""

    total_learners: int
    rows: tuple[LearnerRank, ...]
    #: Hours at the top, the median and the bottom of the ranked population.
    #: Returned beside the ranking rather than derived from the rows shown: the
    #: median of the visible fifty is not the median of the population, and a
    #: reader would have no way to tell which one they were looking at.
    hours_max: Decimal | None
    hours_median: Decimal | None
    hours_min: Decimal | None
    truncated: bool
    filters_applied: str


@dataclass(frozen=True)
class LearnerMatch:
    """One search hit.

    Typed, and named the way `LearnerRank` names the same attributes, because
    the two lists sit on one screen. This was a bare `dict` of column names
    until 8 September: the query selected `full_name` and `department_name`,
    the screen read `name` and `department`, and every match rendered as an
    empty row. Nothing could catch it — an untyped dict at the boundary is a
    contract neither mypy nor TypeScript can check.
    """

    employee_key: int
    employee_code: str | None
    name: str | None
    department: str | None
    company: str | None


@dataclass(frozen=True)
class AttendedProgram:
    crm_program_id: int
    title: str
    sessions: int
    hours: Decimal | None
    first_attended: date | None
    last_attended: date | None


@dataclass(frozen=True)
class LearnerProfile:
    employee_key: int
    employee_code: str | None
    name: str | None
    department: str | None
    company: str | None
    sector: str | None
    job_level: str | None
    position: str | None
    on_current_roster: bool
    figures: tuple[MetricValue, ...]
    programs: tuple[AttendedProgram, ...]
    filters_applied: str


class UnknownLearner(KeyError):
    """No such person, or nobody by that key with attendance in scope."""


def _ranked(filters: MetricFilters) -> Select[tuple[int, int, int, Decimal]]:
    """Learners with any attendance in scope, ordered stably.

    Grouped from `scope.attendances`, so the population is the same one every
    attendance-grain metric counts: soft-deleted rows and unresolved identities
    are already out, and a learner filter — if the caller set one — has already
    narrowed it.

    The second sort key is the whole point. Without it, equal hours come back in
    whatever order the plan happens to produce, and "the same query twice"
    stops meaning the same list.
    """
    rows = scope.attendances(filters).subquery()
    return (
        select(
            rows.c.employee_key,
            func.count(func.distinct(rows.c.crm_program_id)).label("programs"),
            func.count().label("sessions"),
            func.coalesce(func.sum(rows.c.learning_hours), 0).label("hours"),
        )
        .where(rows.c.employee_key.is_not(None))
        .group_by(rows.c.employee_key)
        .order_by(
            func.coalesce(func.sum(rows.c.learning_hours), 0).desc(),
            rows.c.employee_key.asc(),
        )
    )


def top_learners(
    session: Session, filters: MetricFilters | None = None, *, limit: int = DEFAULT_LIMIT
) -> TopLearners:
    """The ranking, derived rather than typed (P-09, P-10).

    The whole population is ranked and the first `limit` of it returned, so
    narrowing the filters changes who is in the ranking rather than how much of
    it is visible. `truncated` says when there is more below.
    """
    applied = filters or MetricFilters()
    ranked = _ranked(applied).subquery()

    total = int(session.scalar(select(func.count()).select_from(ranked)) or 0)
    high, low, median = session.execute(
        select(
            func.max(ranked.c.hours),
            func.min(ranked.c.hours),
            func.percentile_cont(0.5).within_group(ranked.c.hours.asc()),
        )
    ).one()

    hours_max = None if high is None else Decimal(str(high))
    hours_min = None if low is None else Decimal(str(low))
    hours_median = None if median is None else Decimal(str(median))
    described = applied.describe()

    capped = max(1, min(limit, MAX_LIMIT))
    listed = session.execute(
        select(
            ranked.c.employee_key,
            ranked.c.programs,
            ranked.c.sessions,
            ranked.c.hours,
            DimEmployee.employee_code,
            DimEmployee.full_name,
            DimEmployee.department_name,
            DimEmployee.company_name,
        )
        .join(DimEmployee, DimEmployee.employee_key == ranked.c.employee_key)
        .order_by(ranked.c.hours.desc(), ranked.c.employee_key.asc())
        .limit(capped)
    ).all()

    rows: list[LearnerRank] = []
    for index, row in enumerate(listed):
        hours = Decimal(str(row.hours))
        # Competition ranking: equal hours share a rank, and the rank after a
        # tie skips. Computed from the row above rather than from the sort
        # position, so three people on 135.0 are all first.
        rank = rows[-1].rank if rows and rows[-1].hours == hours else index + 1
        rows.append(
            LearnerRank(
                rank=rank,
                employee_key=row.employee_key,
                employee_code=row.employee_code,
                name=row.full_name,
                department=row.department_name,
                company=row.company_name,
                programs=int(row.programs),
                sessions=int(row.sessions),
                hours=hours,
            )
        )

    return TopLearners(
        total_learners=total,
        rows=tuple(rows),
        hours_max=hours_max,
        hours_median=hours_median,
        hours_min=hours_min,
        truncated=total > len(rows),
        filters_applied=described,
    )


def _figures(session: Session, filters: MetricFilters) -> tuple[MetricValue, ...]:
    values: list[MetricValue] = []
    for key in PROFILE_FIGURES:
        try:
            values.append(registry.compute(key, session, filters))
        except UnsupportedFilter:
            continue
    return tuple(values)


def _programs(session: Session, filters: MetricFilters) -> tuple[AttendedProgram, ...]:
    """What this person attended, one row per programme.

    Grouped from the same scoped attendance the figures use, so the hours in
    this table sum to the Learner Hours figure above it.
    """
    rows = scope.attendances(filters).subquery()
    result = session.execute(
        select(
            DimProgram.crm_program_id,
            DimProgram.title,
            func.count(func.distinct(rows.c.crm_session_id)).label("sessions"),
            func.coalesce(func.sum(rows.c.learning_hours), 0).label("hours"),
            func.min(rows.c.attended_date).label("first_attended"),
            func.max(rows.c.attended_date).label("last_attended"),
        )
        .join(DimProgram, DimProgram.crm_program_id == rows.c.crm_program_id)
        .group_by(DimProgram.crm_program_id, DimProgram.title)
        .order_by(func.max(rows.c.attended_date).desc(), DimProgram.title)
    ).mappings()
    return tuple(
        AttendedProgram(
            crm_program_id=row["crm_program_id"],
            title=row["title"],
            sessions=int(row["sessions"]),
            hours=Decimal(str(row["hours"])),
            first_attended=row["first_attended"],
            last_attended=row["last_attended"],
        )
        for row in result
    )


def profile(
    session: Session, employee_key: int, filters: MetricFilters | None = None
) -> LearnerProfile:
    """One person's record: their figures, and the programmes behind them.

    Opened deliberately, one person at a time, rather than produced as a list —
    which is the distinction the gate on `top_learners` draws. Every figure is
    `registry.compute` narrowed to this person, so a profile total equals the
    same metric filtered to them by construction rather than by agreement.
    """
    person = session.get(DimEmployee, employee_key)
    if person is None:
        raise UnknownLearner(f"no learner {employee_key}")

    pinned = (filters or MetricFilters()).narrowed_to(Dimension.LEARNER, str(employee_key))
    return LearnerProfile(
        employee_key=person.employee_key,
        employee_code=person.employee_code,
        name=person.full_name,
        department=person.department_name,
        company=person.company_name,
        sector=person.sector,
        job_level=person.job_level_name,
        position=person.position_name,
        on_current_roster=person.on_current_roster,
        figures=_figures(session, pinned),
        programs=_programs(session, pinned),
        filters_applied=pinned.describe(),
    )


def find(session: Session, query: str, limit: int = 20) -> list[LearnerMatch]:
    """Look up a person by name or employee code.

    The way into one person without reading a ranking at all. Requires
    something to search for: an empty query returns nothing rather than the
    first twenty people, because "everybody, alphabetically" is a roster and
    nobody asked for one.

    Restricted to people with attendance, like the ranking — a search that
    returned somebody who has never attended would lead to a profile with
    nothing on it.
    """
    text = query.strip()
    if not text:
        return []

    pattern = f"%{text.lower()}%"
    attended = (
        select(FactAttendance.employee_key)
        .where(FactAttendance.deleted_at_source.is_(None))
        .distinct()
    )
    rows = session.execute(
        select(
            DimEmployee.employee_key,
            DimEmployee.employee_code,
            DimEmployee.full_name,
            DimEmployee.department_name,
            DimEmployee.company_name,
        )
        .where(
            DimEmployee.is_current,
            DimEmployee.employee_key.in_(attended),
            func.lower(func.coalesce(DimEmployee.full_name, "")).like(pattern)
            | func.lower(func.coalesce(DimEmployee.employee_code, "")).like(pattern),
        )
        .order_by(DimEmployee.full_name)
        .limit(limit)
    ).all()
    return [
        LearnerMatch(
            employee_key=row.employee_key,
            employee_code=row.employee_code,
            name=row.full_name,
            department=row.department_name,
            company=row.company_name,
        )
        for row in rows
    ]


__all__ = [
    "AttendedProgram",
    "LearnerMatch",
    "LearnerProfile",
    "LearnerRank",
    "TopLearners",
    "UnknownLearner",
    "find",
    "profile",
    "top_learners",
]
