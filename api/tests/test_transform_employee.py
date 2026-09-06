"""`core.dim_employee` — SCD Type 2, and the headcount it exists to answer.

The versioning tests are the important ones. The CRM keeps no employee history,
so every version this table holds is one that was observed and would otherwise
have been lost — and a bug that silently overwrote instead of versioning would
look exactly like a working transform until somebody asked a question about
February.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from lnd.db import Base
from lnd.ingest import landing
from lnd.ingest.models import Entity, RawRecord, Source
from lnd.models.app_ import Quarantine, QuarantineReason
from lnd.models.core import DimEmployee
from lnd.models.ops import SourcePresence
from lnd.transform import employee
from lnd.transform.accounting import TransformInvariantError

MONDAY = dt.datetime(2026, 2, 2, 9, 0, tzinfo=dt.UTC)
TUESDAY = MONDAY + dt.timedelta(days=1)
WEDNESDAY = MONDAY + dt.timedelta(days=2)


def user(code: str = "TAI-1001", **overrides: Any) -> dict[str, Any]:
    """One `get_users` object, shaped as the live endpoint returns it."""
    payload: dict[str, Any] = {
        "odoo_id": 4001,
        "employee_code": code,
        "full_name": "Nour Hassan",
        "email": "nour@example.com",
        "mobile": "+201000000000",
        "department": {"id": 12, "name": "Sales"},
        "company": {"id": 3, "name": "The Address Investments"},
        "position": {"id": 88, "name": "Senior Consultant"},
        "sector": "Real Estate",
        "job_level_name": "Senior",
        "job_level_grade": "9",
        "status": "active",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def core_db(db: Session) -> Session:
    """The raw fixture, plus the tables this transform writes."""
    Base.metadata.create_all(
        db.get_bind(),
        tables=[DimEmployee.__table__, Quarantine.__table__, SourcePresence.__table__],
        checkfirst=True,
    )
    db.execute(text("TRUNCATE core.dim_employee, app.quarantine, ops.source_presence"))
    return db


def _land(session: Session, *users: dict[str, Any], at: dt.datetime = MONDAY) -> None:
    """Put a roster into `raw`, which is the transform's only input.

    `land` stamps `fetched_at` from the real clock — correctly, since it records
    when the pull happened — so these tests restamp it onto their own timeline
    afterwards. Without that the fixed dates below would all be *older* than the
    stamp, and `current()`'s `ORDER BY fetched_at DESC` would keep returning the
    first version landed no matter how many followed it.

    Every call restamps, including the default, so two versions landed in one
    test are ordered by id rather than by an accident of which one got the
    earlier microsecond.
    """
    high_water = session.scalar(select(func.coalesce(func.max(RawRecord.id), 0))) or 0
    landing.land(
        session,
        source=Source.CRM,
        entity=Entity.EMPLOYEE,
        records=[(str(u["odoo_id"]), u) for u in users],
    )
    session.execute(update(RawRecord).where(RawRecord.id > high_water).values(fetched_at=at))
    session.flush()


def _versions(session: Session, code: str) -> list[DimEmployee]:
    return list(
        session.scalars(
            select(DimEmployee)
            .where(DimEmployee.employee_code == code)
            .order_by(DimEmployee.valid_from)
        ).all()
    )


class TestFirstBuild:
    def test_a_new_employee_becomes_one_open_version(self, core_db: Session) -> None:
        _land(core_db, user())

        tally = employee.build(core_db, as_of=MONDAY)

        row = _versions(core_db, "TAI-1001")[0]
        assert (row.is_current, row.valid_to, row.valid_from) == (True, None, MONDAY)
        assert tally.received == tally.counted == 1

    def test_attributes_are_conformed_not_carried_verbatim(self, core_db: Session) -> None:
        """The whole reason conforming happens here rather than in a query."""
        _land(core_db, user(sector="  Real Estate ", job_level_grade="G7"))

        employee.build(core_db, as_of=MONDAY)

        row = _versions(core_db, "TAI-1001")[0]
        assert row.sector == "Real Estate"
        assert row.job_level_grade == 7

    def test_the_nested_objects_are_flattened(self, core_db: Session) -> None:
        _land(core_db, user())

        employee.build(core_db, as_of=MONDAY)

        row = _versions(core_db, "TAI-1001")[0]
        assert (row.department_name, row.department_odoo_id) == ("Sales", 12)
        assert (row.company_name, row.company_odoo_id) == ("The Address Investments", 3)
        assert row.position_name == "Senior Consultant"


class TestVersioning:
    def test_an_unchanged_roster_opens_no_new_version(self, core_db: Session) -> None:
        """Idempotence is what makes it safe to run after every sync rather than
        only after a change — and at a 30-minute cadence, a transform that
        versioned on every pass would write 48 rows a day per person."""
        _land(core_db, user())
        employee.build(core_db, as_of=MONDAY)

        tally = employee.build(core_db, as_of=TUESDAY)

        assert len(_versions(core_db, "TAI-1001")) == 1
        assert tally.unchanged == 1
        assert tally.counted == 1

    def test_a_changed_attribute_closes_the_old_version_and_opens_a_new_one(
        self, core_db: Session
    ) -> None:
        _land(core_db, user())
        employee.build(core_db, as_of=MONDAY)

        _land(core_db, user(department={"id": 15, "name": "Marketing"}), at=TUESDAY)
        employee.build(core_db, as_of=TUESDAY)

        first, second = _versions(core_db, "TAI-1001")
        assert (first.department_name, first.valid_to, first.is_current) == (
            "Sales",
            TUESDAY,
            False,
        )
        assert (second.department_name, second.valid_to, second.is_current) == (
            "Marketing",
            None,
            True,
        )

    def test_the_periods_meet_without_a_gap(self, core_db: Session) -> None:
        """A gap would make an as-of query between the two return nobody, and a
        headcount that silently loses a person for an instant is worse than one
        that is obviously wrong."""
        _land(core_db, user())
        employee.build(core_db, as_of=MONDAY)
        _land(core_db, user(status="inactive"), at=TUESDAY)
        employee.build(core_db, as_of=TUESDAY)

        first, second = _versions(core_db, "TAI-1001")
        assert first.valid_to == second.valid_from

    def test_the_database_refuses_two_open_versions(self, core_db: Session) -> None:
        """Enforced by a partial unique index, not by the transform being
        careful. Two current rows for one person would double every count that
        joins them and nothing downstream could detect it."""
        from sqlalchemy.exc import IntegrityError

        _land(core_db, user())
        employee.build(core_db, as_of=MONDAY)

        core_db.add(
            DimEmployee(
                employee_code="TAI-1001",
                odoo_id=4001,
                full_name="Nour Hassan",
                valid_from=TUESDAY,
                is_current=True,
                is_estimated=False,
            )
        )
        with pytest.raises(IntegrityError):
            core_db.flush()

    def test_status_is_versioned_because_it_moves_the_denominator(self, core_db: Session) -> None:
        """active → inactive is the single most consequential change here."""
        _land(core_db, user())
        employee.build(core_db, as_of=MONDAY)
        _land(core_db, user(status="inactive"), at=TUESDAY)
        employee.build(core_db, as_of=TUESDAY)

        assert [row.status for row in _versions(core_db, "TAI-1001")] == ["active", "inactive"]


class TestQuarantine:
    def test_a_row_without_an_employee_code_is_queued_not_dropped(self, core_db: Session) -> None:
        _land(core_db, user(employee_code=None))

        tally = employee.build(core_db, as_of=MONDAY)

        queued = core_db.scalars(select(Quarantine)).one()
        assert queued.reason is QuarantineReason.MISSING_REQUIRED
        assert tally.quarantined == 1
        assert tally.balances

    def test_the_queued_row_keeps_the_payload_that_produced_it(self, core_db: Session) -> None:
        """So somebody can act on it without going back to the source."""
        _land(core_db, user(employee_code=None, full_name="Kareem Adel"))

        employee.build(core_db, as_of=MONDAY)

        assert core_db.scalars(select(Quarantine)).one().payload["full_name"] == "Kareem Adel"

    def test_one_bad_row_does_not_stop_the_good_ones(self, core_db: Session) -> None:
        _land(core_db, user("TAI-1001"), user("TAI-1002", odoo_id=4002, employee_code=None))

        tally = employee.build(core_db, as_of=MONDAY)

        assert core_db.scalar(select(func.count()).select_from(DimEmployee)) == 1
        assert (tally.received, tally.counted, tally.quarantined) == (2, 1, 1)

    def test_a_freelancer_is_counted_rather_than_queued(self, core_db: Session) -> None:
        """Grade 0 is a sentinel, not corruption. Rejecting these removed 20
        active employees from the denominator on the first live run."""
        _land(core_db, user(job_level_grade="0", job_level_name="Freelancer"))

        tally = employee.build(core_db, as_of=MONDAY)

        row = _versions(core_db, "TAI-1001")[0]
        assert (row.job_level_grade, row.job_level_name) == (None, "Freelancer")
        assert tally.quarantined == 0

    def test_two_payloads_claiming_one_person_is_an_ambiguity_not_a_merge(
        self, core_db: Session
    ) -> None:
        """There is no rule for choosing between them, and choosing arbitrarily
        means the roster changes shape between runs for no visible reason."""
        _land(core_db, user("TAI-1001"), user("TAI-1001", odoo_id=4002))

        tally = employee.build(core_db, as_of=MONDAY)

        assert core_db.scalar(select(func.count()).select_from(DimEmployee)) == 1
        assert core_db.scalars(select(Quarantine)).one().reason is QuarantineReason.DUPLICATE
        assert tally.balances


class TestTheInvariant:
    def test_every_landed_row_leaves_by_one_of_two_doors(self, core_db: Session) -> None:
        _land(
            core_db,
            user("TAI-1001"),
            user("TAI-1002", odoo_id=4002),
            user("TAI-1003", odoo_id=4003, employee_code=None),
            user("TAI-1004", odoo_id=4004, job_level_grade="senior"),
        )

        tally = employee.build(core_db, as_of=MONDAY)

        assert tally.received == 4
        assert tally.counted + tally.quarantined == 4

    def test_a_transform_that_dropped_rows_would_fail_the_run(
        self, core_db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The mechanism, demonstrated rather than trusted: break the accounting
        and the run raises instead of committing a short dimension."""
        _land(core_db, user())
        monkeypatch.setattr(employee.RunTally, "count", lambda self, n=1: None)

        with pytest.raises(TransformInvariantError):
            employee.build(core_db, as_of=MONDAY)


class TestLeavers:
    def _mark_gone(self, session: Session, odoo_id: int) -> None:
        session.add(
            SourcePresence(
                source=Source.CRM,
                entity=Entity.EMPLOYEE,
                source_id=str(odoo_id),
                first_seen_at=MONDAY,
                last_seen_at=MONDAY,
                is_present=False,
                vanished_at=TUESDAY,
            )
        )
        session.flush()

    def test_someone_the_source_stopped_returning_is_closed(self, core_db: Session) -> None:
        _land(core_db, user())
        employee.build(core_db, as_of=MONDAY)
        self._mark_gone(core_db, 4001)

        employee.build(core_db, as_of=WEDNESDAY, close_absent=True)

        row = _versions(core_db, "TAI-1001")[-1]
        assert (row.is_current, row.valid_to) == (False, WEDNESDAY)

    def test_presence_decides_it_rather_than_this_run_s_roster(self, core_db: Session) -> None:
        """Absence from one pull is ambiguous — a partial fetch, a quarantined
        row — and only a full reconcile may write `is_present = false`. Stating
        the rule once means `raw` and `core` cannot disagree about who left."""
        _land(core_db, user())
        employee.build(core_db, as_of=MONDAY)

        employee.build(core_db, as_of=WEDNESDAY, close_absent=True)

        assert _versions(core_db, "TAI-1001")[-1].is_current is True

    def test_a_partial_load_closes_nobody(self, core_db: Session) -> None:
        _land(core_db, user())
        employee.build(core_db, as_of=MONDAY)
        self._mark_gone(core_db, 4001)

        employee.build(core_db, as_of=WEDNESDAY, close_absent=False)

        assert _versions(core_db, "TAI-1001")[-1].is_current is True

    def test_a_leaver_does_not_unbalance_the_invariant(self, core_db: Session) -> None:
        """A leaver is the absence of a row. Folding it into the tally would
        make received and counted disagree by exactly the number of people who
        left — an alarm that fires on the pipeline working correctly."""
        _land(core_db, user())
        employee.build(core_db, as_of=MONDAY)
        self._mark_gone(core_db, 4001)

        employee.build(core_db, as_of=WEDNESDAY, close_absent=True).assert_balanced()


class TestTwoSources:
    """The roster and the programs payload answer different questions.

    `get_users` is who works here, and carries no position, email or mobile for
    anybody. The programs payload carries all three, plus the 149 of 419 trained
    people the roster no longer returns — 126 leavers and 23 from two companies
    that no longer exist as separate entities.
    """

    def _program(self, *users: dict[str, Any], program_id: int = 900) -> dict[str, Any]:
        return {
            "id": program_id,
            "title": "Hard Talks",
            "users": [{"is_enrolled": True, "user": u} for u in users],
        }

    def _land_program(self, session: Session, payload: dict[str, Any]) -> None:
        high_water = session.scalar(select(func.coalesce(func.max(RawRecord.id), 0))) or 0
        landing.land(
            session,
            source=Source.CRM,
            entity=Entity.PROGRAM,
            records=[(str(payload["id"]), payload)],
        )
        session.execute(
            update(RawRecord).where(RawRecord.id > high_water).values(fetched_at=MONDAY)
        )
        session.flush()

    def test_someone_who_trained_but_left_is_still_in_the_dimension(self, core_db: Session) -> None:
        """Without this, a third of every attendance figure fails to key and
        quarantines — the numerator silently losing a third of itself."""
        self._land_program(core_db, self._program(user("TAI-9999", odoo_id=9999)))

        employee.build(core_db, as_of=MONDAY)

        row = _versions(core_db, "TAI-9999")[0]
        assert row.on_current_roster is False

    def test_a_leaver_is_excluded_from_the_denominator(self, core_db: Session) -> None:
        """Present so facts can key to them; absent from headcount so they do
        not inflate the population that could have attended."""
        _land(core_db, user("TAI-1001", odoo_id=4001))
        self._land_program(core_db, self._program(user("TAI-9999", odoo_id=9999)))

        employee.build(core_db, as_of=MONDAY)

        assert core_db.scalar(select(func.count()).select_from(DimEmployee)) == 2
        assert employee.headcount_as_of(core_db, as_of=TUESDAY).total == 1

    def test_position_comes_from_the_programs_payload(self, core_db: Session) -> None:
        """The roster carries no position for anyone — all 1,430 rows had it
        null. It is not an override; it is the only source there is."""
        roster_row = user("TAI-1001", odoo_id=4001)
        del roster_row["position"]
        _land(core_db, roster_row)
        self._land_program(
            core_db,
            self._program(user("TAI-1001", odoo_id=4001, position={"id": 5, "name": "Analyst"})),
        )

        employee.build(core_db, as_of=MONDAY)

        row = _versions(core_db, "TAI-1001")[0]
        assert row.position_name == "Analyst"
        assert row.on_current_roster is True

    def test_the_roster_wins_where_both_have_an_opinion(self, core_db: Session) -> None:
        """A program payload is a snapshot from whenever that program ran; the
        roster is today. Letting the older copy win would silently move people
        back into departments they have left."""
        _land(core_db, user("TAI-1001", odoo_id=4001, department={"id": 15, "name": "Marketing"}))
        self._land_program(
            core_db,
            self._program(user("TAI-1001", odoo_id=4001, department={"id": 12, "name": "Sales"})),
        )

        employee.build(core_db, as_of=MONDAY)

        assert _versions(core_db, "TAI-1001")[0].department_name == "Marketing"

    def test_a_person_in_both_sources_is_one_received_row(self, core_db: Session) -> None:
        """Not two. The program copy fills fields in; it does not arrive as a
        separate identity, and counting it as one would break the invariant on
        every run."""
        _land(core_db, user("TAI-1001", odoo_id=4001))
        self._land_program(core_db, self._program(user("TAI-1001", odoo_id=4001)))

        tally = employee.build(core_db, as_of=MONDAY)

        assert tally.received == 1
        assert tally.balances

    def test_appearing_on_many_programs_is_still_one_person(self, core_db: Session) -> None:
        """1,060 user objects for 419 people on the live data."""
        self._land_program(core_db, self._program(user("TAI-9999", odoo_id=9999), program_id=900))
        self._land_program(core_db, self._program(user("TAI-9999", odoo_id=9999), program_id=901))

        tally = employee.build(core_db, as_of=MONDAY)

        assert tally.received == 1
        assert len(_versions(core_db, "TAI-9999")) == 1

    def test_sector_is_conformed_from_the_programs_payload_too(self, core_db: Session) -> None:
        """P-05 is only half fixed. The roster now arrives clean, but the
        programs payload still carries a trailing space on 948 of 1,060."""
        self._land_program(
            core_db, self._program(user("TAI-9999", odoo_id=9999, sector="Finance "))
        )

        employee.build(core_db, as_of=MONDAY)

        assert _versions(core_db, "TAI-9999")[0].sector == "Finance"

    def test_someone_rejoining_the_roster_opens_a_new_version(self, core_db: Session) -> None:
        """The flag is versioned like any other attribute, so "left in March,
        back in July" is answerable rather than overwritten."""
        self._land_program(core_db, self._program(user("TAI-1001", odoo_id=4001)))
        employee.build(core_db, as_of=MONDAY)

        _land(core_db, user("TAI-1001", odoo_id=4001), at=TUESDAY)
        employee.build(core_db, as_of=TUESDAY)

        before, after = _versions(core_db, "TAI-1001")
        assert (before.on_current_roster, after.on_current_roster) == (False, True)


class TestHeadcount:
    def _roster(self, session: Session) -> None:
        _land(
            session,
            user("TAI-1001", odoo_id=4001),
            user("TAI-1002", odoo_id=4002),
            user("MRQ-2001", odoo_id=5001, company={"id": 7, "name": "The MarQ Communities"}),
            user("TAI-1003", odoo_id=4003, status="inactive"),
        )
        employee.build(session, as_of=MONDAY)

    def test_it_counts_the_version_live_at_the_moment_asked(self, core_db: Session) -> None:
        self._roster(core_db)

        assert employee.headcount_as_of(core_db, as_of=TUESDAY).total == 3

    def test_it_breaks_down_by_company(self, core_db: Session) -> None:
        self._roster(core_db)

        counts = employee.headcount_as_of(core_db, as_of=TUESDAY).by_company
        assert counts == {"The Address Investments": 2, "The MarQ Communities": 1}

    def test_leavers_are_excluded_by_status(self, core_db: Session) -> None:
        """Inactive people are not the denominator: participation is over those
        who could attend."""
        self._roster(core_db)

        assert employee.headcount_as_of(core_db, as_of=TUESDAY).total == 3
        assert employee.headcount_as_of(core_db, as_of=TUESDAY, active_only=False).total == 4

    def test_restricting_the_entity_set_narrows_the_denominator(self, core_db: Session) -> None:
        """P-13. Attendance spans five companies, so a headcount restricted to
        one understates participation by roughly a factor of three — which is
        how the workbook arrived at 66.7% for a figure that is 18.9%."""
        self._roster(core_db)

        one = employee.headcount_as_of(
            core_db, as_of=TUESDAY, entity_set=frozenset({"The MarQ Communities"})
        )
        assert one.total == 1

    def test_a_date_before_the_first_snapshot_is_flagged_estimated(self, core_db: Session) -> None:
        """The CRM has no history, so the answer for January is the earliest
        roster carried backwards. That is the only answer available and it is
        not the same kind of fact as one after go-live, so the flag travels with
        the number rather than being something a caller must remember."""
        self._roster(core_db)

        before = employee.headcount_as_of(core_db, as_of=MONDAY - dt.timedelta(days=30))
        after = employee.headcount_as_of(core_db, as_of=TUESDAY)

        assert before.is_estimated is True
        assert after.is_estimated is False

    def test_an_empty_dimension_answers_zero_and_says_it_is_estimated(
        self, core_db: Session
    ) -> None:
        """Rather than raising. A denominator of zero is a real state before the
        first sync, and the flag is what stops it being read as a measurement."""
        result = employee.headcount_as_of(core_db, as_of=TUESDAY)

        assert (result.total, result.is_estimated) == (0, True)

    def test_someone_with_no_company_is_still_counted(self, core_db: Session) -> None:
        """An employee whose company the source did not send is still an
        employee. Dropping them would understate the denominator, which is the
        one direction that overstates participation."""
        _land(core_db, user(company=None))
        employee.build(core_db, as_of=MONDAY)

        result = employee.headcount_as_of(core_db, as_of=TUESDAY)
        assert result.total == 1
        assert result.by_company == {employee.UNKNOWN_COMPANY: 1}
