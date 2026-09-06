"""What a metric counts over, declared rather than inherited.

FR-A05c, and the defence that actually prevents P-03 from recurring.

P-03 was not a formula error. NPS in the workbook averaged rows scoring +1, 0
and -1, which already equals `(promoters - detractors) / n`. The published
figure was wrong because it was computed over a silently filtered 55 of 77
responses — a filter applied somewhere up the sheet and inherited by everything
below it, with nothing on screen to say so.

So no filter here is ever inherited. A metric names its population, that
population is part of what the metric returns, and any narrowing is an argument
somebody passed on purpose. A metric that wanted a different population would
have to declare a different one, which is a code change and a review rather than
a stray click.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Grain(StrEnum):
    """What one row of the population is.

    Named because it decides what a count means. "1,165" is attendances,
    "650" is people, and the workbook's central fault was a sheet where both
    lived in one column (P-06).
    """

    PROGRAM = "program"
    SESSION = "session"
    ENROLLMENT = "enrollment"
    ATTENDANCE = "attendance"
    EVALUATION = "evaluation"
    EMPLOYEE = "employee"


@dataclass(frozen=True)
class Population:
    """The rows a metric is computed over, and the rows it deliberately is not.

    `excludes` is not documentation. It is the sentence somebody reads when the
    number looks wrong, and every entry in it is a decision that would otherwise
    be invisible: soft-deleted rows, unresolved identities, people who have left.
    """

    key: str
    grain: Grain
    description: str
    excludes: tuple[str, ...] = ()

    def __str__(self) -> str:
        return self.description


# Every fact table carries `deleted_at_source`, set by the nightly reconcile
# when a record stops being returned. Excluding it is not optional: a program
# cancelled at source must leave the numerator, and it is stated on every
# population rather than assumed by every query.
_SOFT_DELETED = "records soft-deleted at source by the nightly reconcile"
_UNRESOLVED = "rows whose person could not be identified — quarantined, never dropped"

COMPLETED_PROGRAMS = Population(
    key="completed_programs",
    grain=Grain.PROGRAM,
    description="programs the CRM marks completed",
    excludes=(
        _SOFT_DELETED,
        # `status` and `computed_status` disagree on 55 of 57 programs. Counting
        # off `status` yields 2 where the answer is 55, so every program metric
        # reads `computed_status` and this is where that is written down.
        "programs still upcoming or in progress, by computed_status not status",
    ),
)

ALL_PROGRAMS = Population(
    key="all_programs",
    grain=Grain.PROGRAM,
    description="every program the CRM holds, at any status",
    excludes=(_SOFT_DELETED,),
)

DELIVERED_SESSIONS = Population(
    key="delivered_sessions",
    grain=Grain.SESSION,
    description="sessions belonging to completed programs",
    excludes=(_SOFT_DELETED, "sessions of programs that are not complete"),
)

ENROLLMENTS = Population(
    key="enrollments",
    grain=Grain.ENROLLMENT,
    description="people enrolled on a program",
    excludes=(_SOFT_DELETED, _UNRESOLVED),
)

ATTENDANCES = Population(
    key="attendances",
    grain=Grain.ATTENDANCE,
    description="one person attending one session",
    excludes=(_SOFT_DELETED, _UNRESOLVED),
)

EVALUATIONS = Population(
    key="evaluations",
    grain=Grain.EVALUATION,
    description="survey responses, one per person per program",
    excludes=(
        _SOFT_DELETED,
        _UNRESOLVED,
        # The whole of P-03 in one line. A quality score is computed over every
        # response in scope and never over a subset that a filter upstream
        # happened to leave behind.
        "nothing else — a quality score is over every response in scope",
    ),
)

RATED_EVALUATIONS = Population(
    key="rated_evaluations",
    grain=Grain.EVALUATION,
    description="survey responses carrying a rating for the dimension asked about",
    excludes=(
        _SOFT_DELETED,
        _UNRESOLVED,
        # Not the same exclusion as a filter. A response that did not answer
        # this question has no opinion to average; counting it as a zero would
        # invent one. The response count travels with the score so the size of
        # this set is always visible.
        "responses that left this question blank",
    ),
)

ENROLLABLE_EMPLOYEES = Population(
    key="enrollable_employees",
    grain=Grain.EMPLOYEE,
    description="active employees the CRM can enroll, as of the period end",
    excludes=(
        # P-01: the workbook divided by a hardcoded 192.
        "any fixed number — the denominator is counted, never typed",
        # P-13: it then divided five companies' attendance by one company's
        # headcount. Both halves now come from the same population.
        "people the roster no longer returns — 149 leavers and two closed companies",
        "employees whose status is not active",
    ),
)

ALL_POPULATIONS = (
    COMPLETED_PROGRAMS,
    ALL_PROGRAMS,
    DELIVERED_SESSIONS,
    ENROLLMENTS,
    ATTENDANCES,
    EVALUATIONS,
    RATED_EVALUATIONS,
    ENROLLABLE_EMPLOYEES,
)
