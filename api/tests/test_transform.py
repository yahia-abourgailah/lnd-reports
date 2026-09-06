"""raw -> core, end to end, against a real PostgreSQL.

Every test here starts by landing a payload in `raw` and ends by asserting on
`core` and `ops`. That is deliberate: it is the same path the platform runs
every half hour, so a test that passes is evidence about the pipeline rather
than about a function signature.

The workbook defects each have a test named for them.
"""

from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lnd.ingest.landing import land
from lnd.ingest.models import Entity, Source
from lnd.models.app_ import EnrichmentField, ProgramOverride, SurveyQuestionMap, TrainerAlias
from lnd.models.core import (
    DimEmployee,
    DimProgram,
    DimSession,
    DimTrainer,
    EvaluationDimension,
    FactAttendance,
    FactEnrollment,
    FactEvaluation,
    IdentityStatus,
    ValueSource,
)
from lnd.models.ops import DqException, DqRule, DqStatus
from lnd.transform.runner import transform_programs
from tests.fixtures import crm_program

pytestmark = pytest.mark.usefixtures("core_db")


def land_program(session: Session, payload: dict[str, Any]) -> None:
    land(
        session,
        source=Source.CRM,
        entity=Entity.PROGRAM,
        records=[(str(payload["id"]), payload)],
    )


def author(session: Session, row: object) -> None:
    """Add an `app` overlay row, as the enrichment screen will in week 6."""
    session.add(row)
    session.flush()


def open_rules(session: Session) -> set[DqRule]:
    return {
        row.rule
        for row in session.execute(
            select(DqException).where(DqException.status == DqStatus.OPEN)
        ).scalars()
    }


# ---------------------------------------------------------------------------
# the shred
# ---------------------------------------------------------------------------
class TestFanOut:
    def test_one_nested_program_becomes_three_fact_grains(self, core_db: Session) -> None:
        """The reason `raw`'s grain and `core`'s grain are allowed to differ.

        One raw row in; a dimension row, two sessions, two enrollments, one
        attendance and one evaluation out. The BRD assumed five flat entities
        and the live API returns a tree, so the transform is a shredder.
        """
        land_program(core_db, crm_program.program())

        result = transform_programs(core_db)

        assert result.programs_read == 1
        assert result.programs_transformed == 1
        assert result.sessions == 2
        assert result.enrollments == 2
        assert result.attendance == 1
        assert result.evaluations == 1

    def test_a_second_pass_over_unchanged_data_writes_the_same_rows(self, core_db: Session) -> None:
        """FR-A10, and the reason every grain is a unique constraint.

        Beat runs this every thirty minutes over the same 55 programs. If a
        re-run duplicated anything, every hour metric would climb all day with
        nothing to show for it.
        """
        land_program(core_db, crm_program.program())
        transform_programs(core_db)

        counts_before = {
            model: core_db.scalar(select(func.count()).select_from(model))
            for model in (DimProgram, DimSession, FactEnrollment, FactAttendance, FactEvaluation)
        }

        transform_programs(core_db)

        for model, before in counts_before.items():
            assert core_db.scalar(select(func.count()).select_from(model)) == before

    def test_the_transform_never_calls_a_source(self, core_db: Session) -> None:
        """Replayability, asserted rather than promised.

        Everything comes from `raw`, so a transform fix ships during a CRM
        outage and history rebuilds without a single request.
        """
        land_program(core_db, crm_program.program())
        # No client is constructed and no fixture server is running; if the
        # transform reached for one it would fail here rather than pass.
        assert transform_programs(core_db).programs_transformed == 1


# ---------------------------------------------------------------------------
# the workbook defects
# ---------------------------------------------------------------------------
class TestP02TitleKeying:
    def test_two_programs_sharing_a_title_stay_two_programs(self, core_db: Session) -> None:
        """The workbook's pivots grouped by title, so two separately-run
        "Hard Talks" merged into one and one of them ceased to exist in every
        published figure. The primary key is the fix."""
        first = crm_program.program(id=718, title="Hard Talks")
        second = crm_program.program(id=719, title="Hard Talks")
        # Distinct session ids: the second program is a different delivery.
        second["sessions"] = [dict(s, id=s["id"] + 100) for s in second["sessions"]]

        land_program(core_db, first)
        land_program(core_db, second)
        transform_programs(core_db)

        titles = core_db.execute(select(DimProgram.title)).scalars().all()
        assert titles == ["Hard Talks", "Hard Talks"]
        assert core_db.scalar(select(func.count()).select_from(DimProgram)) == 2


class TestP05WhitespaceVariants:
    def test_a_trailing_space_does_not_invent_a_sector(self, core_db: Session) -> None:
        """`Commercial ` and `Commercial` are one sector, conformed on ingest."""
        payload = crm_program.program()
        land_program(core_db, payload)
        transform_programs(core_db)

        sectors = set(core_db.execute(select(DimEmployee.sector)).scalars().all())
        assert sectors == {"Commercial", "Finance"}
        assert "Commercial " not in sectors


class TestP04TrainerVariants:
    def test_each_spelling_gets_its_own_trainer_until_a_person_merges_them(
        self, core_db: Session
    ) -> None:
        """The machine does not guess that two names are one person.

        It cannot: guessing wrongly would combine two real trainers' scorecards
        with nothing to show it happened. So two spellings are two trainers,
        visibly, until somebody authors the merge.
        """
        payload = crm_program.program()
        payload["sessions"][1]["trainer_name"] = "mona  SAEED"
        land_program(core_db, payload)
        transform_programs(core_db)

        # Case and whitespace carry no information, so these conform to one.
        assert core_db.scalar(select(func.count()).select_from(DimTrainer)) == 1

    def test_an_authored_alias_merges_two_genuinely_different_spellings(
        self, core_db: Session
    ) -> None:
        """FR-B06. The merge is a decision, so it lives in `app`."""
        payload = crm_program.program()
        payload["sessions"][1]["trainer_name"] = "M. Saeed"
        land_program(core_db, payload)
        transform_programs(core_db)

        assert core_db.scalar(select(func.count()).select_from(DimTrainer)) == 2

        canonical = core_db.execute(
            select(DimTrainer.trainer_key).where(DimTrainer.canonical_name == "Mona Saeed")
        ).scalar_one()
        author(
            core_db,
            TrainerAlias(
                normalised_name="m. saeed",
                trainer_key=canonical,
                authored_by="specialist@example.test",
            ),
        )

        transform_programs(core_db)

        # Every session now points at one trainer, and the whole history moved
        # with the alias rather than needing a correction.
        keys = set(core_db.execute(select(DimSession.trainer_key)).scalars().all())
        assert keys == {canonical}


class TestP12SessionIdentity:
    def test_training_days_counts_distinct_session_ids(self, core_db: Session) -> None:
        """The workbook's `#` column was never a session key — 117 values over
        415 rows. Session identity comes from `session.id` and nowhere else."""
        land_program(core_db, crm_program.program())
        transform_programs(core_db)

        assert set(core_db.execute(select(DimSession.crm_session_id)).scalars()) == {543, 544}


class TestTwoHourMetrics:
    def test_training_hours_and_learner_hours_are_different_numbers(self, core_db: Session) -> None:
        """The workbook tracked both — 130.5 and 1,386 — and named neither.

        Two four-hour sessions were delivered, so Training Hours Delivered is
        8. One person attended one of them, so Learner Hours is 4. Both are
        correct, for different questions.
        """
        land_program(core_db, crm_program.program())
        transform_programs(core_db)

        training_hours = core_db.scalar(select(func.sum(DimSession.duration_hours)))
        learner_hours = core_db.scalar(select(func.sum(FactAttendance.learning_hours)))

        assert training_hours == Decimal("8.00")
        assert learner_hours == Decimal("4.00")
        assert training_hours != learner_hours


class TestRosterIsNotEnrollment:
    def test_a_walk_in_attends_without_being_enrolled(self, core_db: Session) -> None:
        """`users[]` is the union of enrolled people, walk-ins and respondents.

        Treating it as the enrollment list would inflate the funnel's first
        step and understate No-show Rate.
        """
        payload = crm_program.program()
        payload["users"][1]["is_enrolled"] = False
        land_program(core_db, payload)
        transform_programs(core_db)

        assert core_db.scalar(select(func.count()).select_from(FactEnrollment)) == 1


# ---------------------------------------------------------------------------
# counted or excepted, never neither
# ---------------------------------------------------------------------------
class TestQuarantine:
    def test_an_unresolvable_attendee_is_counted_and_excepted_never_dropped(
        self, core_db: Session
    ) -> None:
        """P-07. The workbook lost 38 of these and said nothing.

        The attendance row exists, so Total Participants is right. Its
        `employee_key` is null, so every breakdown by person excludes it. And
        an exception names it, so the exclusion is visible and fixable.
        """
        payload = crm_program.program()
        payload["sessions"][0]["attendance"][0]["user"] = None
        payload["sessions"][0]["attendance"][0]["user_odoo_id"] = "9999"
        land_program(core_db, payload)

        transform_programs(core_db)

        row = core_db.execute(
            select(FactAttendance).where(FactAttendance.employee_odoo_id == "9999")
        ).scalar_one()
        assert row.employee_key is None
        assert row.identity_status is IdentityStatus.UNRESOLVED

        assert DqRule.IDENTITY_UNRESOLVED in open_rules(core_db)

    def test_a_session_missing_a_time_is_excluded_from_hours_and_excepted(
        self, core_db: Session
    ) -> None:
        """FR-B03. The session still exists; only its hours are absent."""
        payload = crm_program.program()
        payload["sessions"][0]["session_time_to"] = None
        land_program(core_db, payload)

        transform_programs(core_db)

        row = core_db.execute(
            select(DimSession).where(DimSession.crm_session_id == 543)
        ).scalar_one()
        assert row.duration_hours is None
        assert row.duration_derivable is False
        # And its attendee contributes no Learner Hours, rather than zero hours
        # that would look like a real, tiny session.
        attendance = core_db.execute(
            select(FactAttendance).where(FactAttendance.crm_session_id == 543)
        ).scalar_one()
        assert attendance.learning_hours is None

        assert DqRule.DURATION_UNDERIVABLE in open_rules(core_db)

    def test_a_double_scan_is_counted_once_and_excepted(self, core_db: Session) -> None:
        """P-09, and the guarantee the fact grain provides."""
        payload = crm_program.program()
        duplicate = copy.deepcopy(payload["sessions"][0]["attendance"][0])
        duplicate["id"] = 1168
        payload["sessions"][0]["attendance"].append(duplicate)
        land_program(core_db, payload)

        transform_programs(core_db)

        assert core_db.scalar(select(func.count()).select_from(FactAttendance)) == 1
        assert DqRule.DUPLICATE_ATTENDANCE in open_rules(core_db)

    def test_a_customised_program_with_no_department_is_counted_not_quarantined(
        self, core_db: Session
    ) -> None:
        """It is in the totals and out of the department breakdown.

        The disposition matters: FR-F04's completeness indicator would
        understate the platform's coverage if this were treated as a loss.
        """
        payload = crm_program.program(target="department", departments=[])
        land_program(core_db, payload)

        transform_programs(core_db)

        assert DqRule.CUSTOMISED_DEPT_MISSING in open_rules(core_db)
        exception = core_db.execute(
            select(DqException).where(DqException.rule == DqRule.CUSTOMISED_DEPT_MISSING)
        ).scalar_one()
        assert exception.costs_numbers is False


class TestExceptionLifecycle:
    def test_the_same_violation_across_passes_is_one_row(self, core_db: Session) -> None:
        """Without a stable key the queue grows by a copy of itself every pass."""
        payload = crm_program.program(target="department", departments=[])
        land_program(core_db, payload)

        transform_programs(core_db)
        transform_programs(core_db)
        transform_programs(core_db)

        rows = (
            core_db.execute(
                select(DqException).where(DqException.rule == DqRule.CUSTOMISED_DEPT_MISSING)
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].occurrences == 3

    def test_authoring_the_fix_closes_the_exception_on_the_next_pass(
        self, core_db: Session
    ) -> None:
        """The queue is a picture of the present, not a log.

        An operator does not close an exception — they author the fix, and the
        next pass finds the rule satisfied and closes the row itself. A row
        closed by hand while the data still violated the rule would reopen half
        an hour later looking like a new problem.
        """
        payload = crm_program.program(target="department", departments=[])
        land_program(core_db, payload)
        transform_programs(core_db)
        assert DqRule.CUSTOMISED_DEPT_MISSING in open_rules(core_db)

        author(
            core_db,
            ProgramOverride(
                crm_program_id=718,
                field=EnrichmentField.CUSTOMISED_DEPARTMENT,
                value="Sales",
                authored_by="specialist@example.test",
            ),
        )

        transform_programs(core_db)

        assert DqRule.CUSTOMISED_DEPT_MISSING not in open_rules(core_db)
        program = core_db.get(DimProgram, 718)
        assert program is not None
        assert program.customised_department_name == "Sales"
        assert program.customised_department_source is ValueSource.OVERRIDE


class TestOverlayPrecedence:
    def test_the_crm_value_wins_over_an_override(self, core_db: Session) -> None:
        """FR-C04, and why an override needs no migration to retire.

        When the CRM started supplying `departments[]`, every hand-entered
        department became redundant. The column simply stopped reading them,
        and `..._source` says so without anyone diffing two tables.
        """
        payload = crm_program.program(
            target="department",
            departments=[{"id": 214413, "odoo_id": "77", "name": "Sales"}],
        )
        land_program(core_db, payload)
        author(
            core_db,
            ProgramOverride(
                crm_program_id=718,
                field=EnrichmentField.CUSTOMISED_DEPARTMENT,
                value="Marketing",
                authored_by="specialist@example.test",
            ),
        )

        transform_programs(core_db)

        program = core_db.get(DimProgram, 718)
        assert program is not None
        assert program.customised_department_name == "Sales"
        assert program.customised_department_source is ValueSource.CRM


# ---------------------------------------------------------------------------
# evaluations
# ---------------------------------------------------------------------------
class TestEvaluationMapping:
    def test_an_unmapped_scored_question_is_excepted_not_guessed(self, core_db: Session) -> None:
        """The BRD's q1-q5 do not exist in this CRM.

        Each program carries its own survey with its own question ids, and
        nothing in the payload says which question is the facilitator question.
        A guess would silently move four published percentages, so an unmapped
        scored question goes to the queue instead.
        """
        land_program(core_db, crm_program.program())

        transform_programs(core_db)

        assert DqRule.SURVEY_QUESTION_UNMAPPED in open_rules(core_db)
        evaluation = core_db.execute(select(FactEvaluation)).scalar_one()
        assert evaluation.unmapped_question_count == 1
        assert evaluation.score_knowledge_relevance is None

    def test_a_mapped_select_answer_needs_an_option_score(self, core_db: Session) -> None:
        """ "Very useful" is not a number, and which words mean four is a
        judgement about one survey's wording — so it is authored, not inferred.
        """
        land_program(core_db, crm_program.program())
        author(
            core_db,
            SurveyQuestionMap(
                crm_survey_id=43,
                crm_question_id=42,
                question_title="How useful was the program?",
                dimension=EvaluationDimension.KNOWLEDGE_RELEVANCE,
                scale_min=1,
                scale_max=5,
                authored_by="specialist@example.test",
            ),
        )

        transform_programs(core_db)

        assert DqRule.SURVEY_QUESTION_UNMAPPED not in open_rules(core_db)
        assert DqRule.SURVEY_OPTION_UNSCORED in open_rules(core_db)

    def test_a_free_text_answer_is_a_comment_not_a_loss(self, core_db: Session) -> None:
        """It needs no mapping: the scorecard prints it and nothing aggregates
        over it, so it is not excluded from anything."""
        payload = crm_program.program()
        payload["users"][0]["survey_answers"][0] = {
            "id": 283,
            "question_id": 44,
            "question_title": "Anything else?",
            "answer_type": "text",
            "answer": "  The room was cold.  ",
            "selected_option": None,
            "answered_at": "2026-09-02T16:01:23+03:00",
        }
        land_program(core_db, payload)

        transform_programs(core_db)

        evaluation = core_db.execute(select(FactEvaluation)).scalar_one()
        assert evaluation.comment == "The room was cold."
        assert evaluation.unmapped_question_count == 0
        assert DqRule.SURVEY_QUESTION_UNMAPPED not in open_rules(core_db)


# ---------------------------------------------------------------------------
# the SCD
# ---------------------------------------------------------------------------
class TestSlowlyChangingEmployees:
    def test_an_unchanged_person_does_not_open_a_new_version(self, core_db: Session) -> None:
        """Every thirty minutes, forever. A pass that versioned regardless
        would make the table's own history useless."""
        land_program(core_db, crm_program.program())
        transform_programs(core_db)
        transform_programs(core_db)

        assert core_db.scalar(select(func.count()).select_from(DimEmployee)) == 2

    def test_a_changed_attribute_closes_one_version_and_opens_another(
        self, core_db: Session
    ) -> None:
        """FR-B02, and the reason February's participation rate holds still."""
        land_program(core_db, crm_program.program())
        transform_programs(core_db)

        moved = crm_program.program()
        moved["users"][0]["user"] = dict(moved["users"][0]["user"], sector="Residential")
        moved["sessions"][0]["attendance"][0]["user"] = moved["users"][0]["user"]
        land_program(core_db, moved)

        transform_programs(core_db)

        versions = (
            core_db.execute(
                select(DimEmployee)
                .where(DimEmployee.odoo_id == "4821")
                .order_by(DimEmployee.valid_from)
            )
            .scalars()
            .all()
        )
        assert len(versions) == 2
        assert versions[0].sector == "Commercial"
        assert versions[0].is_current is False
        assert versions[0].valid_to is not None
        assert versions[1].sector == "Residential"
        assert versions[1].is_current is True

    def test_exactly_one_current_version_per_person(self, core_db: Session) -> None:
        """Enforced by a partial unique index, because the loader is the thing
        most likely to be wrong. Two current rows would double that person in
        the headcount denominator — the defect class the SCD exists to end."""
        land_program(core_db, crm_program.program())
        transform_programs(core_db)

        current_per_person = core_db.execute(
            select(DimEmployee.odoo_id, func.count())
            .where(DimEmployee.is_current)
            .group_by(DimEmployee.odoo_id)
        ).all()
        assert all(count == 1 for _, count in current_per_person)


# ---------------------------------------------------------------------------
# the calendar
# ---------------------------------------------------------------------------
class TestCalendar:
    def test_every_referenced_date_exists_before_a_fact_needs_it(self, core_db: Session) -> None:
        """A fact date is a foreign key into `dim_date`, so discovering a gap by
        way of a constraint violation is not an acceptable way to find out."""
        land_program(core_db, crm_program.program())
        transform_programs(core_db)

        # The two session dates resolve, which they could not if the calendar
        # were filled lazily.
        assert core_db.execute(
            select(DimSession.session_date).order_by(DimSession.session_date)
        ).scalars().all() == [date(2026, 9, 10), date(2026, 9, 11)]

    def test_the_months_between_two_clusters_are_filled(self, core_db: Session) -> None:
        """The empty month is the whole reason this dimension exists.

        Group the facts themselves by month and an empty month is simply absent
        from the result, so the chart draws a line across it and the gap reads
        as a dip rather than as no training at all.
        """
        from lnd.models.core import DimDate

        land_program(core_db, crm_program.program())
        transform_programs(core_db)

        # No training happened in October 2026, and October is in the calendar.
        assert core_db.get(DimDate, date(2026, 10, 15)) is not None


# ---------------------------------------------------------------------------
# failure handling
# ---------------------------------------------------------------------------
class TestMalformedPayloads:
    def test_one_bad_payload_does_not_stop_the_others(self, core_db: Session) -> None:
        """`raw` holds what arrived, including whatever arrived malformed.

        One program blocking every figure on the dashboard is the failure mode
        the raw layer was built to prevent; it would be perverse to reintroduce
        it here.
        """
        land_program(core_db, crm_program.program(id=718))
        # A session ending before it starts: rejected by the boundary model.
        broken = crm_program.program(id=719)
        broken["sessions"] = [dict(broken["sessions"][0], id=643, session_time_to="08:00:00")]
        land_program(core_db, broken)

        result = transform_programs(core_db)

        assert result.programs_read == 2
        assert result.programs_transformed == 1
        assert result.programs_invalid == 1
        assert result.invalid_ids == ["719"]
        assert core_db.get(DimProgram, 718) is not None
        assert core_db.get(DimProgram, 719) is None


class TestPartialPass:
    def test_a_narrowed_pass_does_not_resolve_the_whole_queue(self, core_db: Session) -> None:
        """It did not look at the rest of the data.

        Closing every exception it did not raise, on the grounds of not having
        looked, would be exactly the silent loss this layer exists to prevent.
        """
        land_program(core_db, crm_program.program(id=718, target="department", departments=[]))
        land_program(core_db, crm_program.program(id=719))
        transform_programs(core_db)
        assert DqRule.CUSTOMISED_DEPT_MISSING in open_rules(core_db)

        transform_programs(core_db, source_ids=["719"])

        assert DqRule.CUSTOMISED_DEPT_MISSING in open_rules(core_db)
