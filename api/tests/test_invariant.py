"""The week-3 exit criterion, tested: counted or quarantined, never neither.

Two halves, and the second is the one that matters. The first asserts the
arithmetic — that `check_ledger` adds up correctly. The second breaks the
writer on purpose and asserts that the pass refuses to commit, which is the
only evidence that the invariant is load-bearing rather than decorative.

A check that has never been seen to fail is a check nobody has tested.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lnd.ingest.landing import current, land
from lnd.ingest.models import Entity, Source
from lnd.models.core import DimProgram, FactAttendance
from lnd.models.ops import DqException, DqRule, DqStatus
from lnd.sources.crm.models import Program
from lnd.transform.invariant import (
    InvariantViolation,
    Offered,
    check_ledger,
    offered,
    offered_by_all,
)
from lnd.transform.programs import AttendanceOutcome, ProgramTransformer
from lnd.transform.runner import transform_programs
from tests.fixtures import crm_program


def land_program(session: Session, payload: dict[str, Any]) -> None:
    land(
        session,
        source=Source.CRM,
        entity=Entity.PROGRAM,
        records=[(str(payload["id"]), payload)],
    )


def open_rules(session: Session) -> set[DqRule]:
    return {
        row.rule
        for row in session.execute(
            select(DqException).where(DqException.status == DqStatus.OPEN)
        ).scalars()
    }


BALANCED = {
    "programs_written": 1,
    "sessions_written": 2,
    "enrollments_written": 2,
    "attendance_written": 1,
    "attendance_duplicates": 0,
    "evaluations_written": 1,
}


# ---------------------------------------------------------------------------
# counting the payload, independently of the writer
# ---------------------------------------------------------------------------
class TestOffered:
    def test_it_counts_the_fixture_the_writer_also_counts(self) -> None:
        """The two sides agree on the shared fixture, as they must."""
        assert offered(Program.model_validate(crm_program.program())) == Offered(
            programs=1, sessions=2, enrollments=2, attendance=1, evaluations=1
        )

    def test_the_roster_is_not_the_enrollment_list(self) -> None:
        """`users[]` is enrolled plus walk-ins plus respondents.

        Counting it wholesale would inflate the expectation and make every
        pass look like it had lost an enrollment.
        """
        payload = crm_program.program()
        payload["users"][1]["is_enrolled"] = False

        counted = offered(Program.model_validate(payload))

        assert len(payload["users"]) == 2
        assert counted.enrollments == 1

    def test_attendance_is_rows_not_people(self) -> None:
        """One person at two sessions is two attendance rows.

        The grain is per session. Counting distinct people here would let a
        writer drop a second session's attendance without the ledger noticing.
        """
        payload = crm_program.program()
        payload["sessions"][1]["attendance"] = [
            dict(payload["sessions"][0]["attendance"][0], id=1168)
        ]

        assert offered(Program.model_validate(payload)).attendance == 2

    def test_evaluations_are_people_not_answers(self) -> None:
        """The grain is one response per person per program, however many
        questions that response answered."""
        payload = crm_program.program()
        answers = payload["users"][0]["survey_answers"]
        answers.append(dict(answers[0], id=283, question_id=43))

        assert offered(Program.model_validate(payload)).evaluations == 1

    def test_totals_sum_over_the_pass(self) -> None:
        one = Program.model_validate(crm_program.program())
        two = Program.model_validate(crm_program.program(id=719))

        assert offered_by_all([one, two]).sessions == 4


# ---------------------------------------------------------------------------
# the arithmetic
# ---------------------------------------------------------------------------
class TestCheckLedger:
    def test_a_balanced_pass_says_nothing(self) -> None:
        check_ledger(
            Offered(programs=1, sessions=2, enrollments=2, attendance=1, evaluations=1),
            **BALANCED,
        )

    def test_a_missing_row_raises(self) -> None:
        with pytest.raises(InvariantViolation, match="attendance"):
            check_ledger(
                Offered(programs=1, sessions=2, enrollments=2, attendance=2, evaluations=1),
                **BALANCED,
            )

    def test_a_refused_duplicate_balances_the_books(self) -> None:
        """A second scan of one person at one session is offered and not
        written, and that is correct — so it has to be counted, not ignored."""
        check_ledger(
            Offered(programs=1, sessions=2, enrollments=2, attendance=2, evaluations=1),
            **{**BALANCED, "attendance_duplicates": 1},
        )

    def test_an_unexplained_shortfall_is_not_forgiven_by_a_duplicate(self) -> None:
        """Two rows short with one duplicate to explain it is still one row
        lost, and the message says so."""
        with pytest.raises(InvariantViolation, match=r"offered 3, transform accounted for 2"):
            check_ledger(
                Offered(programs=1, sessions=2, enrollments=2, attendance=3, evaluations=1),
                **{**BALANCED, "attendance_duplicates": 1},
            )

    def test_every_failing_grain_is_named_not_just_the_first(self) -> None:
        """Somebody reading this alert at seven in the morning should not fix
        one grain and rediscover the other on the next run."""
        with pytest.raises(InvariantViolation) as caught:
            check_ledger(
                Offered(programs=1, sessions=9, enrollments=2, attendance=1, evaluations=9),
                **BALANCED,
            )

        assert "sessions" in str(caught.value)
        assert "evaluations" in str(caught.value)

    def test_a_surplus_fails_as_loudly_as_a_shortfall(self) -> None:
        """Writing more rows than were offered is duplication, and duplication
        inflates every hour metric all day."""
        with pytest.raises(InvariantViolation, match="enrollments"):
            check_ledger(
                Offered(programs=1, sessions=2, enrollments=1, attendance=1, evaluations=1),
                **BALANCED,
            )


# ---------------------------------------------------------------------------
# the invariant against a real pass
# ---------------------------------------------------------------------------
@pytest.mark.usefixtures("core_db")
class TestAgainstAPass:
    def test_the_ordinary_pass_holds(self, core_db: Session) -> None:
        """If this ever fails, the transform has lost rows — not the test."""
        land_program(core_db, crm_program.program())

        result = transform_programs(core_db)

        assert result.programs_transformed == 1
        assert result.attendance == 1
        assert result.attendance_duplicates == 0

    def test_a_writer_that_drops_a_row_stops_the_pass(
        self, core_db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The test the whole module exists for.

        A writer is sabotaged into silently skipping attendance — exactly what
        a mistaken `continue` or a wrong conflict target would do — and the
        pass must refuse rather than publish a `core` with no attendance in it.
        """
        land_program(core_db, crm_program.program())

        monkeypatch.setattr(
            ProgramTransformer,
            "_write_attendance",
            lambda *args, **kwargs: AttendanceOutcome(attendees=set(), rows=0, duplicates=0),
        )

        with pytest.raises(InvariantViolation, match="attendance"):
            transform_programs(core_db)

    def test_a_lost_payload_stops_the_pass_before_any_grain_is_counted(
        self, core_db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same rule one level up, over payloads rather than rows.

        A payload that fails validation is *not* this defect — that path
        increments `programs_invalid`, so it balances, which is the counter
        being honest. The defect is a payload the pass read and then neither
        transformed nor rejected: a `continue` added to the parse loop by
        somebody who did not know these two counters have to add up. That is
        modelled here by a record set whose length disagrees with what it
        yields.
        """
        land_program(core_db, crm_program.program())

        real = current

        def one_short(*args: Any, **kwargs: Any) -> Any:
            class Miscounted(list):  # type: ignore[type-arg]
                def __len__(self) -> int:
                    return super().__len__() + 1

            return Miscounted(real(*args, **kwargs))

        monkeypatch.setattr("lnd.transform.runner.current", one_short)

        with pytest.raises(InvariantViolation, match="lost payloads"):
            transform_programs(core_db)

    def test_an_invalid_payload_is_accounted_for_rather_than_lost(self, core_db: Session) -> None:
        """A payload that no longer validates is *counted* as rejected, so it
        balances. One bad program must not stop the other fifty-four."""
        land_program(core_db, crm_program.program())
        land_program(core_db, {"id": 719, "title": None})

        result = transform_programs(core_db)

        assert result.programs_read == 2
        assert result.programs_transformed == 1
        assert result.programs_invalid == 1


# ---------------------------------------------------------------------------
# ATTENDEE_OUTSIDE_ROSTER (P-13's detectable half)
# ---------------------------------------------------------------------------
@pytest.mark.usefixtures("core_db")
class TestAttendeeOutsideRoster:
    @staticmethod
    def _payload_with_a_stranger() -> dict[str, Any]:
        """Somebody scanned into a session who is in no roster entry."""
        payload = crm_program.program()
        payload["sessions"][1]["attendance"] = [
            {
                "id": 1169,
                "user_odoo_id": "9999",
                "user": None,
                "attended_at": "2026-09-11T09:05:00+03:00",
            }
        ]
        return payload

    def test_a_stranger_is_excepted(self, core_db: Session) -> None:
        land_program(core_db, self._payload_with_a_stranger())

        transform_programs(core_db)

        assert DqRule.ATTENDEE_OUTSIDE_ROSTER in open_rules(core_db)

    def test_they_are_counted_in_attendance_not_dropped(self, core_db: Session) -> None:
        """P-07's lesson: the row exists and is excluded by a join failing,
        never by an absence. Their hours are real hours."""
        land_program(core_db, self._payload_with_a_stranger())

        result = transform_programs(core_db)

        assert result.attendance == 2
        rows = core_db.scalar(
            select(func.count())
            .select_from(FactAttendance)
            .where(FactAttendance.employee_odoo_id == "9999")
        )
        assert rows == 1

    def test_a_walk_in_is_not_a_stranger(self, core_db: Session) -> None:
        """The distinction the two rules exist to keep apart.

        A walk-in is in `users[]` with `is_enrolled` false — we know their
        sector and can report on them. Reporting them as outside the roster
        would put a correction in the queue that nobody can act on.
        """
        payload = crm_program.program()
        payload["users"][0]["is_enrolled"] = False

        land_program(core_db, payload)
        transform_programs(core_db)

        rules = open_rules(core_db)
        assert DqRule.ATTENDANCE_NO_ENROLLMENT in rules
        assert DqRule.ATTENDEE_OUTSIDE_ROSTER not in rules

    def test_one_stranger_is_one_queue_item_however_many_sessions(self, core_db: Session) -> None:
        """Keyed on the person, not on the scan: one roster correction to make,
        so one row to act on."""
        payload = self._payload_with_a_stranger()
        payload["sessions"][0]["attendance"].append(
            {
                "id": 1170,
                "user_odoo_id": "9999",
                "user": None,
                "attended_at": "2026-09-10T09:05:00+03:00",
            }
        )

        land_program(core_db, payload)
        transform_programs(core_db)

        rows = core_db.scalar(
            select(func.count())
            .select_from(DqException)
            .where(DqException.rule == DqRule.ATTENDEE_OUTSIDE_ROSTER)
        )
        assert rows == 1

    def test_a_clean_program_raises_nothing(self, core_db: Session) -> None:
        """Measured against live data the rule fires zero times today. It must
        stay quiet until something is genuinely wrong."""
        land_program(core_db, crm_program.program())

        transform_programs(core_db)

        assert DqRule.ATTENDEE_OUTSIDE_ROSTER not in open_rules(core_db)
        assert core_db.scalar(select(func.count()).select_from(DimProgram)) == 1
