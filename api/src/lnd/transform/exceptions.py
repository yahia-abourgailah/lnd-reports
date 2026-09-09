"""The exception queue: counted or excepted, never neither (FR-F05).

The workbook's most dangerous behaviour was silent loss. Thirty-eight attendees
had no employee code, so the sector join dropped them, so they vanished from
every sector breakdown — and nothing anywhere said so. The reports were not
wrong in a way anyone could see; they were wrong in a way nobody could see.

This module is the answer, and its rule is absolute: **if the transform excludes
a record from a metric, it registers an exception for it in the same pass.** A
number is allowed to be incomplete. It is not allowed to be quietly incomplete.

THE KEY IS THE WHOLE DESIGN

The transform re-runs over the same 55 programs on every pass. A rule violated
once is violated every time, so without a stable key the queue would grow by a
copy of itself every thirty minutes and a dismissal would last until the next
beat tick. `exception_key` names the *violation* — the rule plus the identifiers
of the thing violating it — and never carries a timestamp, a count, or a run id.
Raising the same violation twice bumps `last_seen_at` and `occurrences` on the
row that is already there.

WHAT A PASS THAT STOPS SEEING A VIOLATION DOES

It resolves it. `sweep_resolved` closes every open exception the pass did not
re-raise, which is what makes the queue a picture of the present rather than a
log. That inverts the usual ordering: an operator does not close an exception,
they *author the fix* — an identity mapping, a trainer alias, a department
override — and the next pass finds the rule satisfied and closes the row
itself. A row closed by hand while the data still violated the rule would
reopen half an hour later looking like a new problem.

A dismissal is the exception to that, and is never reopened by a pass. It is a
person saying "yes, and that is fine", which is a fact about the world that no
amount of re-reading the payload can contradict.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from lnd.models.ops import DqException, DqRule, DqStatus
from lnd.quality import catalogue

log = logging.getLogger(__name__)

# Read from the catalogue rather than declared here. Two lists of "which rules
# cost numbers" is how one of them comes to disagree with the exclusion banner —
# which reported three flagged records as three excluded ones for a week.
DISPOSITIONS = catalogue.DISPOSITIONS


@dataclass
class ExceptionRecorder:
    """Collects the pass's exceptions, then reconciles the queue against them.

    Deliberately *not* a function that writes each exception as it is found.
    Resolution needs to know the complete set of violations this pass saw, and
    a streaming writer cannot know that until it has finished — so the pass
    accumulates and `flush` does the reconciliation in one place, where the
    "raise what is new, close what is gone" pair can be read together.
    """

    session: Session
    #: Every key raised this pass. A set, because a rule fired for the same
    #: record twice within one pass is one violation, not two.
    seen: set[str] = field(default_factory=set)
    _pending: list[dict[str, Any]] = field(default_factory=list)

    def raise_for(
        self,
        rule: DqRule,
        *,
        key_parts: tuple[str | int | None, ...],
        summary: str,
        crm_program_id: int | None = None,
        crm_session_id: int | None = None,
        employee_odoo_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Register one violation. Safe to call for the same thing twice."""
        key = ":".join([rule.value, *(str(part) for part in key_parts if part is not None)])
        if key in self.seen:
            return
        self.seen.add(key)
        self._pending.append(
            {
                "exception_key": key,
                "rule": rule,
                "disposition": DISPOSITIONS[rule],
                "status": DqStatus.OPEN,
                "crm_program_id": crm_program_id,
                "crm_session_id": crm_session_id,
                "employee_odoo_id": employee_odoo_id,
                "summary": summary[:500],
                "details": details,
            }
        )

    def flush(self, *, resolve_absent: bool = True) -> tuple[int, int, int]:
        """Write the pass's exceptions and reconcile the queue.

        Returns (raised, still_open, resolved).

        `resolve_absent` is false for a partial pass — one program, say, from a
        drill-through or a targeted re-run. Closing "every open exception this
        pass did not raise" is only correct when the pass looked at everything;
        a single-program run would otherwise resolve the whole queue on the
        grounds that it did not look.
        """
        now = datetime.now(UTC)
        raised = self._write(now)
        resolved = self._resolve_absent(now) if resolve_absent else 0
        still_open = len(self.seen)

        log.info(
            "exception queue reconciled",
            extra={
                "event": "transform.exceptions.flushed",
                "raised": raised,
                "still_open": still_open,
                "resolved": resolved,
            },
        )
        return raised, still_open, resolved

    # -- internals ---------------------------------------------------------
    def _write(self, now: datetime) -> int:
        """Upsert every pending violation, returning how many were new.

        `ON CONFLICT DO UPDATE` rather than a read-then-write: two passes may
        overlap, and a read-then-write would have one of them insert a
        duplicate key and fail an otherwise good transform.

        A dismissed row is left alone by the `WHERE` clause. Re-raising it
        would undo a person's decision on the next beat tick, which is the
        fastest way to teach an operator that the queue does not listen.
        """
        if not self._pending:
            return 0

        statement = (
            insert(DqException)
            .values(self._pending)
            .on_conflict_do_update(
                constraint="uq_dq_exception_key",
                set_={
                    "last_seen_at": now,
                    "occurrences": DqException.__table__.c.occurrences + 1,
                    # A resolved violation that recurs reopens this same row,
                    # keeping its history and its first_seen_at — "this has
                    # been happening on and off since March" is a more useful
                    # thing to know than a fresh row every time.
                    "status": DqStatus.OPEN,
                    "closed_at": None,
                },
                where=DqException.__table__.c.status != DqStatus.DISMISSED.value,
            )
            .returning(DqException.id, DqException.occurrences)
        )
        rows = self.session.execute(statement).all()
        self._pending.clear()
        # `occurrences` is 1 exactly when the row was inserted rather than
        # bumped, so the insert/update split needs no second query. A dismissed
        # row returns nothing at all — the WHERE excluded it — and so counts as
        # neither, which is right: nothing happened to it.
        return sum(1 for _, occurrences in rows if occurrences == 1)

    def _resolve_absent(self, now: datetime) -> int:
        """Close every open exception this pass did not re-raise.

        This is what makes the queue current rather than cumulative. An
        operator who authors an identity mapping sees the exception disappear
        on the next pass, without anyone having to remember to close it.
        """
        statement = update(DqException).where(DqException.status == DqStatus.OPEN)
        if self.seen:
            # A pass that raised nothing resolves everything, and that is
            # correct — it looked at all the data and found no violations. The
            # branch exists only because `NOT IN ()` is not valid SQL.
            statement = statement.where(DqException.exception_key.not_in(self.seen))

        statement = statement.values(status=DqStatus.RESOLVED, closed_at=now).returning(
            DqException.id
        )
        return len(self.session.execute(statement).scalars().all())


def open_exceptions(session: Session, *, rule: DqRule | None = None) -> list[DqException]:
    """The queue as an operator sees it (FR-F01): open issues, oldest first."""
    statement = (
        select(DqException)
        .where(DqException.status == DqStatus.OPEN)
        .order_by(DqException.rule, DqException.first_seen_at)
    )
    if rule is not None:
        statement = statement.where(DqException.rule == rule)
    return list(session.execute(statement).scalars().all())


def dismiss(session: Session, *, exception_key: str, dismissed_by: str, reason: str) -> bool:
    """Record a deliberate "yes, and that is fine" (FR-F03).

    The reason is required rather than optional. An exception dismissed with no
    stated reason is indistinguishable six months later from one dismissed by
    mistake, and NFR-07 requires the attribution regardless.
    """
    result = session.execute(
        update(DqException)
        .where(DqException.exception_key == exception_key)
        .values(
            status=DqStatus.DISMISSED,
            closed_at=datetime.now(UTC),
            closed_by=dismissed_by,
            closed_note=reason,
        )
        .returning(DqException.id)
    )
    return result.scalar_one_or_none() is not None
