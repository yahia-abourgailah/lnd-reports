"""Orchestration: raw -> core, in one transaction.

    read raw -> validate -> calendar -> employees -> programs -> exceptions

THE WHOLE PASS IS ONE TRANSACTION

Deliberately unlike the sync, where landing and auditing commit separately so
that the record of a failure outlives the failure. Here the opposite is
required: `core` is a *pure function* of (raw + enrichment), and a pass that
committed its dimensions and then failed on its facts would leave a `core` that
is not the image of any input at all — programs present, attendance missing, and
every hour metric quietly halved with nothing to say so.

Failing whole means the previous, coherent `core` keeps serving. That is the
same principle as NFR-03's last-known-good: stale and correct beats fresh and
wrong.

NOTHING HERE CALLS A SOURCE SYSTEM

Every input is read back from `raw.source_record`. That is what makes the model
replayable: a transform bug is fixed by changing code and re-running, never by
re-querying the CRM and never by editing a number. It also means this can run
while the CRM is down, which is how a fix ships during an outage.

A PAYLOAD THAT NO LONGER VALIDATES DOES NOT STOP THE PASS

`raw` holds what arrived, including whatever arrived malformed. One program
that fails validation is logged, counted and stepped over; the other
fifty-four still transform. The alternative — one bad payload blocking every
figure on the dashboard — is the failure mode the raw layer was built to
prevent, and it would be perverse to reintroduce it here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy.orm import Session

from lnd.db import session_scope
from lnd.ingest.landing import current
from lnd.ingest.models import Entity, RawRecord, Source
from lnd.sources.crm.models import Program
from lnd.transform.dates import ensure_covering
from lnd.transform.employees import load_employees
from lnd.transform.exceptions import ExceptionRecorder
from lnd.transform.identity import IdentityResolver
from lnd.transform.invariant import (
    InvariantViolation,
    check_against_core,
    check_ledger,
    offered_by_all,
)
from lnd.transform.programs import (
    Overlay,
    ProgramTransformer,
    TrainerRegistry,
    collect_users,
    program_dates,
)

log = logging.getLogger(__name__)


@dataclass
class TransformResult:
    """What the pass did, in the shape the Celery task returns and logs."""

    programs_read: int = 0
    programs_transformed: int = 0
    programs_invalid: int = 0
    employees_seen: int = 0
    employees_versioned: int = 0
    sessions: int = 0
    enrollments: int = 0
    attendance: int = 0
    evaluations: int = 0
    attendees: int = 0
    #: Attendance rows the source offered and the grain refused — a second scan
    #: of one person at one session. The only route by which an offered fact row
    #: does not reach `core`, and therefore the only subtrahend the invariant
    #: has.
    attendance_duplicates: int = 0
    trainers_created: int = 0
    dates_added: int = 0
    exceptions_raised: int = 0
    exceptions_open: int = 0
    exceptions_resolved: int = 0
    invalid_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, int]:
        """Counters only — what beat logs and what the task returns."""
        return {key: value for key, value in self.__dict__.items() if isinstance(value, int)}


def _parse(record: RawRecord) -> Program | None:
    """One raw payload to a validated program, or None with the reason logged."""
    try:
        return Program.model_validate(record.payload)
    except ValidationError as exc:
        log.error(
            "raw payload no longer validates; skipped",
            extra={
                "event": "transform.payload.invalid",
                "source_id": record.source_id,
                "raw_record_id": record.id,
                "errors": exc.error_count(),
            },
        )
        return None


def transform_programs(session: Session, *, source_ids: list[str] | None = None) -> TransformResult:
    """Rebuild `core` from the current contents of `raw`.

    `source_ids` narrows the pass to specific programs — a drill-through, or a
    targeted re-run after fixing one payload. A narrowed pass does *not*
    resolve absent exceptions: it did not look at the rest of the data, and
    closing the whole queue on the grounds of not having looked would be a
    silent loss of exactly the kind this layer exists to prevent.
    """
    partial = source_ids is not None
    result = TransformResult()

    records = current(session, source=Source.CRM, entity=Entity.PROGRAM, source_ids=source_ids)
    result.programs_read = len(records)

    parsed: list[tuple[RawRecord, Program]] = []
    for record in records:
        program = _parse(record)
        if program is None:
            result.programs_invalid += 1
            result.invalid_ids.append(record.source_id)
            continue
        parsed.append((record, program))

    # The same rule one level up, over payloads rather than rows: a raw record
    # is either parsed or counted as invalid. These two counters have existed
    # since the module was written and were never compared, which is how a
    # `continue` added to this loop later would drop a program in silence.
    if len(parsed) + result.programs_invalid != result.programs_read:
        raise InvariantViolation(
            f"transform lost payloads; the pass will not commit — read "
            f"{result.programs_read}, parsed {len(parsed)}, "
            f"rejected {result.programs_invalid}"
        )

    if not parsed:
        log.warning(
            "transform found nothing to do",
            extra={"event": "transform.empty", "read": result.programs_read},
        )
        return result

    # -- the calendar, before anything references it -----------------------
    # Every fact date is a foreign key into dim_date, so the calendar has to
    # cover the whole pass before the first fact is written. Doing it per fact
    # would mean discovering a missing date by way of a constraint violation.
    dates = [day for _, program in parsed for day in program_dates(program)]
    result.dates_added = ensure_covering(session, dates)

    # -- employees, before the facts that resolve against them -------------
    users = {}
    for _, program in parsed:
        users.update(collect_users(program))
    employees = load_employees(session, users)
    result.employees_seen = employees.seen
    result.employees_versioned = employees.versioned

    # Built *after* the employee load, so that somebody appearing for the
    # first time in this pass resolves rather than being quarantined for
    # having been new.
    resolver = IdentityResolver.build(session)
    overlay = Overlay.load(session)
    trainers = TrainerRegistry(session=session, aliases=overlay.trainer_aliases)
    recorder = ExceptionRecorder(session=session)

    transformer = ProgramTransformer(
        session=session,
        resolver=resolver,
        overlay=overlay,
        trainers=trainers,
        exceptions=recorder,
    )

    for record, program in parsed:
        counts = transformer.transform(program, raw_record_id=record.id)
        result.programs_transformed += counts.programs
        result.sessions += counts.sessions
        result.enrollments += counts.enrollments
        result.attendance += counts.attendance
        result.evaluations += counts.evaluations
        result.attendees += counts.attendees
        result.attendance_duplicates += counts.attendance_duplicates

    result.trainers_created = trainers.created

    # -- the invariant, before anything is allowed to commit ----------------
    # Counted or quarantined, never neither. `offered_by_all` re-reads the
    # payloads independently of everything above, so agreement here is evidence
    # that the writer handled every row rather than a restatement of what it
    # thinks it did. A violation raises out of `session_scope`, the transaction
    # rolls back, and yesterday's coherent `core` keeps serving.
    expected = offered_by_all([program for _, program in parsed])
    check_ledger(
        expected,
        programs_written=result.programs_transformed,
        sessions_written=result.sessions,
        enrollments_written=result.enrollments,
        attendance_written=result.attendance,
        attendance_duplicates=result.attendance_duplicates,
        evaluations_written=result.evaluations,
    )

    # The stronger check, and only sound for a full pass: a narrowed one has
    # not read the other fifty-four programs, whose rows are rightly in these
    # tables and rightly absent from `expected`.
    if not partial:
        session.flush()
        check_against_core(session, expected)

    raised, still_open, resolved = recorder.flush(resolve_absent=not partial)
    result.exceptions_raised = raised
    result.exceptions_open = still_open
    result.exceptions_resolved = resolved

    log.info(
        "transform complete",
        extra={"event": "transform.complete", **result.as_dict()},
    )
    return result


def run_transform(*, source_ids: list[str] | None = None) -> TransformResult:
    """Entry point for the worker: one pass in its own transaction."""
    started = datetime.now(UTC)
    try:
        with session_scope() as session:
            result = transform_programs(session, source_ids=source_ids)
    except InvariantViolation as exc:
        # Logged at its own event rather than left to the generic task failure,
        # because this is a correctness alarm and not a capacity one: nothing
        # is retried, nothing is degraded, and a person has to look. The
        # rollback has already happened by the time this runs — `core` is
        # exactly as it was before the pass started.
        log.error(
            "transform invariant violated; core left untouched",
            extra={"event": "transform.invariant.violated", "detail": str(exc)},
        )
        raise

    log.info(
        "transform pass finished",
        extra={
            "event": "transform.finished",
            "duration_seconds": (datetime.now(UTC) - started).total_seconds(),
        },
    )
    return result
