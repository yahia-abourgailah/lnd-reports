"""CRM models, against the real payload shape from the API document.

Validation at the boundary. The workbook's failure mode was that a malformed
value flowed silently into a pivot and out into a published number; these tests
assert the opposite — bad data is refused where it arrives, with the payload
already stored in `raw` for diagnosis.
"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal

import pytest
from pydantic import ValidationError

from lnd.sources.crm import (
    Program,
    ProgramsPage,
    ProgramStatus,
    ProgramTarget,
    ProgramType,
    parse_page,
    parse_programs,
    program_id_of,
)
from tests.fixtures import crm_program


# --------------------------------------------------------------------- programs
def test_the_documented_payload_parses() -> None:
    p = Program.model_validate(crm_program.program())

    assert p.id == 718
    assert p.title == "Negotiation Essentials"
    assert p.type is ProgramType.INTERNAL
    assert p.target is ProgramTarget.PUBLIC
    assert p.status is ProgramStatus.UPCOMING
    assert p.start_date == date(2026, 9, 10)
    assert p.track is not None and p.track.title == "Sales Excellence"


def test_two_programs_sharing_a_title_stay_distinct() -> None:
    """P-02. The CRM ran Hard Talks twice, as #87 and #81. The workbook grouped
    by title and merged them. Identity here is the CRM id, so they cannot."""
    first = Program.model_validate(crm_program.program(id=87, title="Hard Talks"))
    second = Program.model_validate(crm_program.program(id=81, title="Hard Talks"))
    assert first.title == second.title
    assert first.id != second.id


def test_completion_uses_computed_status_not_status() -> None:
    """The document is explicit: `status` is a stored value and is not
    recomputed as sessions pass, so it can still read `upcoming` after the last
    session has ended. Counting Total Programs off it would undercount."""
    p = Program.model_validate(crm_program.program(status="upcoming", computed_status="completed"))
    assert p.status is ProgramStatus.UPCOMING
    assert p.counts_toward_total_programs


def test_a_stored_completed_but_computed_upcoming_does_not_count() -> None:
    p = Program.model_validate(crm_program.program(status="completed", computed_status="upcoming"))
    assert not p.counts_toward_total_programs


@pytest.mark.parametrize("value", ["Internal", "INTERNAL", "vendor"])
def test_an_unrecognised_type_is_refused(value: str) -> None:
    """The API sends lower case. Anything else is a contract change and must be
    seen rather than silently coerced."""
    with pytest.raises(ValidationError):
        Program.model_validate(crm_program.program(type=value))


def test_an_unknown_status_is_refused() -> None:
    with pytest.raises(ValidationError):
        Program.model_validate(crm_program.program(computed_status="archived"))


def test_a_program_with_no_sessions_parses() -> None:
    """start_date and end_date are null when a program has no sessions yet."""
    p = Program.model_validate(
        crm_program.program(sessions=[], start_date=None, end_date=None, users=[])
    )
    assert p.start_date is None
    assert p.sessions == []


def test_an_unknown_field_is_kept_not_rejected() -> None:
    """A field the CRM adds tomorrow must not stop ingestion."""
    p = Program.model_validate(crm_program.program(brand_new_field="surprise"))
    assert p.title == "Negotiation Essentials"


def test_trailing_whitespace_is_preserved_not_repaired() -> None:
    """P-05 lives in trailing spaces. Normalisation is week 3; erasing the
    evidence here would hide the defect."""
    p = Program.model_validate(crm_program.program(title="Projects "))
    assert p.title == "Projects "


# --------------------------------------------------------------------- sessions
def test_sessions_carry_a_stable_identifier() -> None:
    """P-12. `COUNT(DISTINCT session_key)` is meaningful only because this
    exists — the workbook's `#` column was a row counter and a session id in
    one column."""
    p = Program.model_validate(crm_program.program())
    assert [s.id for s in p.sessions] == [543, 544]
    assert len({s.id for s in p.sessions}) == 2


def test_duration_is_derived_from_the_session_times() -> None:
    p = Program.model_validate(crm_program.program())
    assert p.sessions[0].duration_hours == Decimal("4.00")
    assert p.sessions[0].session_time_from == time(9, 0)


def test_training_hours_delivered_sums_the_sessions() -> None:
    p = Program.model_validate(crm_program.program())
    assert p.training_hours_delivered == Decimal("8.00")


def test_a_session_missing_a_time_has_no_derivable_duration() -> None:
    """DURATION_UNDERIVABLE: excluded from both hour metrics and raised as an
    exception — counted or excepted, never neither."""
    payload = crm_program.program()
    payload["sessions"][0]["session_time_to"] = None
    p = Program.model_validate(payload)

    assert not p.sessions[0].duration_derivable
    assert p.sessions[0].duration_hours is None
    assert len(p.sessions_missing_duration) == 1
    assert p.training_hours_delivered == Decimal("4.00")


def test_an_end_before_its_start_is_refused() -> None:
    """Unchecked this yields a negative duration, which sums into Training
    Hours Delivered and quietly reduces a published figure."""
    payload = crm_program.program()
    payload["sessions"][0]["session_time_to"] = "08:00:00"
    with pytest.raises(ValidationError, match="is not after"):
        Program.model_validate(payload)


def test_the_trainer_arrives_on_the_session_as_free_text() -> None:
    """Q-01 is answered: the CRM does hold the trainer. But it is a string per
    session, not a trainer entity, so name variants still need an alias table
    before any per-trainer figure is published (P-04)."""
    p = Program.model_validate(crm_program.program())
    assert p.trainer_names == {"Mona Saeed"}


def test_a_session_without_a_trainer_parses() -> None:
    payload = crm_program.program()
    payload["sessions"][0]["trainer_name"] = None
    p = Program.model_validate(payload)
    assert p.trainer_names == {"Mona Saeed"}  # the other session still has one


# ----------------------------------------------------------------------- roster
def test_the_roster_is_not_the_enrollment_list() -> None:
    """`users[]` is the union of enrolled, walk-ins and survey-only respondents.
    Counting it as enrollments would inflate the funnel and understate
    No-show Rate."""
    payload = crm_program.program()
    payload["users"].append(
        {
            "user_odoo_id": "5099",
            "user": None,
            "is_enrolled": False,
            "enrolled_at": None,
            "attendance_rate": 100,
            "survey_answers": [],
            "assessment_answers": [],
        }
    )
    p = Program.model_validate(payload)

    assert len(p.users) == 3
    assert len(p.enrolled) == 2
    assert len(p.walk_ins) == 1


def test_a_roster_entry_can_have_no_matching_user() -> None:
    """An attendance or answer row can reference an odoo_id with no CRM user —
    a departed employee, an unsynced record. It must still be counted or
    excepted, never dropped (P-07)."""
    payload = crm_program.program()
    payload["users"][1]["user"] = None
    p = Program.model_validate(payload)

    assert p.users[1].user is None
    assert p.users[1].user_odoo_id == "4977"


def test_unresolved_attendance_is_surfaced() -> None:
    payload = crm_program.program()
    payload["sessions"][0]["attendance"][0]["user"] = None
    p = Program.model_validate(payload)

    assert len(p.unresolved_attendance) == 1
    assert p.unresolved_attendance[0].user_odoo_id == "4821"


def test_the_join_key_is_the_odoo_id() -> None:
    """`user.id` is a CRM-local primary key. `odoo_id` is what HR shares."""
    p = Program.model_validate(crm_program.program())
    entry = p.users[0]
    assert entry.user_odoo_id == "4821"
    assert entry.user is not None
    assert entry.user.odoo_id == entry.user_odoo_id
    assert entry.user.id != int(entry.user_odoo_id)


# ------------------------------------------------------------------ statistics
def test_statistics_are_captured_for_cross_checking() -> None:
    """If our own count of distinct attendees disagrees with the CRM's, one of
    us is wrong and it should fail loudly rather than be reconciled by hand."""
    p = Program.model_validate(crm_program.program())
    assert p.statistics is not None
    assert p.statistics.enrolled_users_count == 2
    assert p.statistics.attended_users_count == 1
    assert p.statistics.attendance_rate == 50


# ------------------------------------------------------------------- feedback
def test_survey_answers_arrive_inside_the_program() -> None:
    """Materially: feedback IS in the CRM. Every quality score and NPS can be
    computed from this payload."""
    p = Program.model_validate(crm_program.program())
    answers = p.users[0].survey_answers

    assert len(answers) == 1
    assert answers[0].question_title == "How useful was the program?"
    assert answers[0].selected_option is not None
    assert answers[0].selected_option.value == "Very useful"


def test_the_survey_definition_arrives_too() -> None:
    p = Program.model_validate(crm_program.program())
    assert p.survey is not None
    assert p.survey.questions[0].options[0].value == "Very useful"


def test_a_program_with_no_survey_parses() -> None:
    p = Program.model_validate(crm_program.program(survey=None, assessment=None))
    assert p.survey is None
    assert p.assessment is None


def test_assessment_answers_are_separate_from_survey_answers() -> None:
    p = Program.model_validate(crm_program.program())
    assert len(p.users[0].assessment_answers) == 1
    assert p.users[0].assessment_answers[0].answer is not None


# ---------------------------------------------------------------- multi-entity
def test_the_company_behind_each_attendee_is_visible() -> None:
    """P-13: attendees span several companies. The denominator for
    participation rate has to cover the same population as the numerator, and
    this is where that population becomes visible."""
    p = Program.model_validate(crm_program.program())
    company = p.users[0].user.company if p.users[0].user else None
    assert company is not None
    assert company.name == "The Address Investments"
    assert company.odoo_id == "1"


def test_a_departmental_program_names_its_department() -> None:
    """Q-02 is answered: the CRM records which department a customised program
    was built for. `departments[]` is empty only when target is `public`."""
    p = Program.model_validate(
        crm_program.program(
            target="department",
            departments=[{"id": 214413, "odoo_id": "77", "name": "Sales", "company_odoo_id": "1"}],
        )
    )
    assert p.target is ProgramTarget.DEPARTMENT
    assert [d.name for d in p.departments] == ["Sales"]


# ----------------------------------------------------------------------- pages
def test_a_whole_page_parses_with_its_meta() -> None:
    page = parse_page(crm_program.page())
    assert isinstance(page, ProgramsPage)
    assert page.meta.current_page == 1
    assert page.meta.has_more_pages is False
    assert page.meta.from_ == 1


def test_an_empty_page_has_null_from_and_to() -> None:
    page = parse_page(crm_program.page(programs=[], total=0, **{"from": None, "to": None}))
    assert page.programs == []
    assert page.meta.from_ is None


def test_parsing_separates_the_good_from_the_bad() -> None:
    """One malformed program must not cost us the other fifty-four."""
    parsed, rejected = parse_programs(
        [
            crm_program.program(id=1),
            crm_program.program(id=2),
            crm_program.program(id=3, computed_status="nonsense"),
        ]
    )
    assert [p.id for p in parsed] == [1, 2]
    assert len(rejected) == 1
    assert rejected[0][0]["id"] == 3


def test_the_raw_key_is_the_program_id_as_text() -> None:
    assert program_id_of(crm_program.program(id=718)) == "718"


# ------------------------------------------------- sector and job level
def test_sector_and_job_level_are_captured() -> None:
    """Added by the CRM team on request. They are the only two attributes the
    coverage view needed that the CRM did not already carry — and the reason
    the HRIS left the plan."""
    p = Program.model_validate(crm_program.program())
    user = p.users[0].user
    assert user is not None
    assert user.sector == "Commercial "
    assert user.job_level_name == "Senior Specialist"


def test_the_job_grade_is_parsed_to_an_integer() -> None:
    """It arrives as text. As strings, "10" sorts before "9", so any ordering
    by seniority would be wrong."""
    p = Program.model_validate(crm_program.program())
    user = p.users[0].user
    assert user is not None
    assert user.job_level_grade == 9
    assert isinstance(user.job_level_grade, int)


def test_sector_keeps_its_whitespace_but_conforms_on_demand() -> None:
    """P-05, reproduced exactly: 940 of 1,052 live user objects carry a
    trailing space, and 28 raw sectors collapse to 23 once trimmed. The raw
    value is preserved as evidence; grouping uses the conformed one."""
    p = Program.model_validate(crm_program.program())
    user = p.users[0].user
    assert user is not None
    assert user.sector == "Commercial "  # exactly as it arrived
    assert user.sector_conformed == "Commercial"  # what a breakdown groups on


def test_a_missing_job_level_is_not_an_error() -> None:
    """Null for roughly one person in ten; they drop out of level breakdowns
    rather than breaking ingestion."""
    p = Program.model_validate(crm_program.program())
    user = p.users[1].user
    assert user is not None
    assert user.job_level_name is None
    assert user.job_level_grade is None
    assert user.has_job_level is False


@pytest.mark.parametrize(
    ("given", "expected"), [("9", 9), (" 14 ", 14), (10, 10), (None, None), ("", None)]
)
def test_grade_parsing_handles_what_the_source_sends(given: object, expected: int | None) -> None:
    from lnd.sources.crm.models import User

    assert User.model_validate({"id": 1, "job_level_grade": given}).job_level_grade == expected
