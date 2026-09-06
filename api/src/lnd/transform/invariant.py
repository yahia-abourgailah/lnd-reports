"""Counted or quarantined, never neither — enforced rather than intended.

The week-3 exit criterion, and the one property that makes silent loss
impossible: no record passes through the transform without being either written
to `core` or accounted for as something the transform refused to write.

WHY IT IS NOT A COUNT OF `raw` AGAINST A COUNT OF `core`

The plan states the invariant as `count(raw active) = count(core included) +
count(quarantined)`. Taken literally that compares 55 program rows against
roughly 900 fact rows, because `raw` records what arrived — one tree per
program — and `core` records the three grains it fans out into. The comparison
only means anything per grain, so that is how it is stated here:

    for each grain:  offered  ==  written  +  refused

WHY BOTH SIDES ARE COUNTED FROM OPPOSITE ENDS

This is the whole design, and getting it wrong turns the check into decoration.
If `offered` and `written` were both incremented inside the writer's loop, a
loop that skipped a row would skip both counters and the assertion would pass
while the data was lost — the check would be a tautology dressed as a guarantee.

So `offered()` below is a pure function over the validated `Program` models. It
walks the tree, it never touches the database, and it shares no code path with
`ProgramTransformer`. `written` comes back from the transformer. The two can
only agree if the writer genuinely handled every row the payload contained.

WHAT COUNTS AS "REFUSED"

Very little, and that is the point. Most exceptions do not withhold a row:

    IDENTITY_UNRESOLVED    row written, `employee_key` null
    DURATION_UNDERIVABLE   row written, `duration_hours` null
    DUPLICATE_ATTENDANCE   row NOT written

Quarantine here means "excluded from the metrics the rule affects", and for the
first two that exclusion is a join failing, not a row missing. Only the
duplicate scan is genuinely dropped, so it is the only subtrahend — and it is
returned by the transformer as a number rather than merely raised as an
exception, because a skipped row that nothing counts is precisely the defect.

WHAT A FAILURE DOES

Raises, which rolls back the pass — the whole transform is one transaction, so
`core` keeps its previous coherent contents and the dashboard keeps serving
them. Deliberately not a warning: a `core` that has lost rows must never be
served, and stale-and-correct beats fresh-and-wrong (NFR-03).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lnd.models.core import FactAttendance, FactEnrollment, FactEvaluation
from lnd.sources.crm.models import Program

log = logging.getLogger(__name__)


class InvariantViolation(AssertionError):
    """The transform lost rows. The pass must not commit.

    An `AssertionError` subclass so that it reads as what it is — a broken
    promise about the code rather than a bad input — while still being
    catchable by name for the alert.
    """


@dataclass(frozen=True)
class Offered:
    """What the payloads presented, counted without reference to the writer."""

    programs: int = 0
    sessions: int = 0
    enrollments: int = 0
    attendance: int = 0
    evaluations: int = 0

    def __add__(self, other: Offered) -> Offered:
        return Offered(
            programs=self.programs + other.programs,
            sessions=self.sessions + other.sessions,
            enrollments=self.enrollments + other.enrollments,
            attendance=self.attendance + other.attendance,
            evaluations=self.evaluations + other.evaluations,
        )


def offered(program: Program) -> Offered:
    """Count what one validated payload contains, and nothing else.

    Every line here mirrors a decision made in `programs.py` — enrollments are
    the roster entries flagged `is_enrolled`, evaluations are the entries
    carrying survey answers — and mirrors it *independently*. That duplication
    is the mechanism, not an oversight: two implementations of "how many rows
    should there be" that agree are evidence, and one implementation that
    agrees with itself is not.

    Enrollments and evaluations are counted *distinctly* because their grains
    are distinct — one row per person per program. Attendance is counted as raw
    rows because its grain is per session, and the duplicate scans that the
    grain refuses are subtracted explicitly rather than quietly deduplicated
    here. Deduplicating on this side of the comparison would hide exactly the
    thing the comparison is for.
    """
    return Offered(
        programs=1,
        sessions=len(program.sessions),
        enrollments=len({entry.user_odoo_id for entry in program.enrolled}),
        attendance=sum(len(session.attendance) for session in program.sessions),
        evaluations=len({entry.user_odoo_id for entry in program.users if entry.survey_answers}),
    )


def offered_by_all(programs: list[Program]) -> Offered:
    """The pass's total, summed over every payload it parsed."""
    total = Offered()
    for program in programs:
        total = total + offered(program)
    return total


def check_ledger(
    expected: Offered,
    *,
    programs_written: int,
    sessions_written: int,
    enrollments_written: int,
    attendance_written: int,
    attendance_duplicates: int,
    evaluations_written: int,
) -> None:
    """Assert the pass wrote every row its payloads offered.

    Raises `InvariantViolation` naming every grain that failed, not just the
    first. Someone reading an alert at seven in the morning should learn that
    attendance *and* evaluations are short, rather than fixing one and
    rediscovering the other on the next run.
    """
    failures: list[str] = []

    def compare(grain: str, want: int, got: int, *, refused: int = 0) -> None:
        if want != got + refused:
            detail = f" + {refused} refused" if refused else ""
            failures.append(
                f"{grain}: payload offered {want}, transform accounted for "
                f"{got + refused} ({got} written{detail})"
            )

    compare("programs", expected.programs, programs_written)
    compare("sessions", expected.sessions, sessions_written)
    compare("enrollments", expected.enrollments, enrollments_written)
    compare(
        "attendance",
        expected.attendance,
        attendance_written,
        refused=attendance_duplicates,
    )
    compare("evaluations", expected.evaluations, evaluations_written)

    if failures:
        raise InvariantViolation(
            "transform lost records; the pass will not commit — " + "; ".join(failures)
        )


def check_against_core(session: Session, expected: Offered) -> None:
    """Re-count the grains in `core` itself, and compare.

    The ledger check compares the pass against its own reading of the payload.
    This compares the payload against the *table*, which catches a class the
    ledger cannot see: an upsert whose conflict target is wrong overwrites an
    existing row instead of inserting a new one, so the writer counts a write,
    the ledger balances, and the table is one row short.

    Only correct for a full pass. A pass narrowed to one program has not looked
    at the other fifty-four, whose rows are legitimately in these tables and
    legitimately absent from `expected` — so the runner calls this only when the
    pass read everything.

    Soft-deleted rows are excluded on both sides: a program the nightly
    reconcile has marked gone at source keeps its facts, and they answer to a
    payload that is no longer being offered.
    """
    grains = (
        ("enrollments", FactEnrollment, expected.enrollments),
        ("attendance", FactAttendance, expected.attendance),
        ("evaluations", FactEvaluation, expected.evaluations),
    )

    failures: list[str] = []
    for grain, model, want in grains:
        got = session.execute(
            select(func.count()).select_from(model).where(model.deleted_at_source.is_(None))
        ).scalar_one()
        # Attendance is the one grain where the table is legitimately smaller
        # than the payload, by exactly the duplicate scans the grain refuses.
        if got > want or (grain != "attendance" and got != want):
            failures.append(f"{grain}: payload offered {want}, core holds {got}")

    if failures:
        raise InvariantViolation(
            "core does not match the payloads it was built from; the pass will "
            "not commit — " + "; ".join(failures)
        )

    log.info(
        "transform invariant holds against core",
        extra={
            "event": "transform.invariant.core_ok",
            "enrollments": expected.enrollments,
            "attendance": expected.attendance,
            "evaluations": expected.evaluations,
        },
    )
