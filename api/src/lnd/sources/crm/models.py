"""Typed models for the CRM Learning Program Dataset API.

Written against `docs/LEARNING_PROGRAM_DATASET_API.md`. One endpoint returns a
whole program per entry, with its sessions, attendance, roster, survey answers
and assessment answers nested inside — so these models mirror that tree rather
than the several flat entities the BRD assumed.

Validation happens here, at the boundary, because this is where bad source data
has to be caught. What these models never do is *repair* a value: a trailing
space stays a trailing space, because the raw layer must record what arrived.
Normalisation is a week-3 transform concern (P-05).

Enum values are lower-case — that is what the API actually sends.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProgramType(StrEnum):
    """Who delivered it. `internal` means delivered by L&D."""

    INTERNAL = "internal"
    EXTERNAL = "external"


class ProgramTarget(StrEnum):
    """`public` is open to everyone; `department` is limited to `departments[]`."""

    PUBLIC = "public"
    DEPARTMENT = "department"


class ProgramStatus(StrEnum):
    UPCOMING = "upcoming"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AnswerType(StrEnum):
    TEXT = "text"
    SELECT = "select"
    RATING = "rating"


class CrmModel(BaseModel):
    """Base for every CRM entity.

    `extra="allow"` is deliberate. A field the CRM adds tomorrow must not break
    ingestion — the raw layer stores the whole payload regardless, and a model
    that rejected unknown keys would turn an additive source change into an
    outage. Contract tests catch the change; the pipeline keeps running.
    """

    model_config = ConfigDict(extra="allow", str_strip_whitespace=False, populate_by_name=True)


# ---------------------------------------------------------------------------
# small nested objects
# ---------------------------------------------------------------------------
class Track(CrmModel):
    id: int
    title: str | None = None
    subtitle: str | None = None
    description: str | None = None
    status: str | None = None


class ParentProgram(CrmModel):
    id: int
    title: str | None = None


class Department(CrmModel):
    id: int
    odoo_id: str | None = None
    name: str | None = None
    company_odoo_id: str | None = None


class Company(CrmModel):
    """The employing entity.

    Directly relevant to P-13: attendees span several companies, so the
    participation-rate denominator must cover the same population as the
    numerator. This is where that population becomes visible.
    """

    id: int
    odoo_id: str | None = None
    name: str | None = None


class Position(CrmModel):
    id: int
    name: str | None = None


class Location(CrmModel):
    id: int
    name: str | None = None


class User(CrmModel):
    """An employee, as the CRM holds them.

    `odoo_id` is the cross-system identifier shared with HR — the join key.
    `user.id` is a CRM-local primary key and must never be joined on.

    Sector and job level were added by the CRM team on request; they are the
    only two attributes the coverage view needs that the CRM did not already
    carry, and their arrival is what removed the HRIS from the plan.
    """

    id: int
    odoo_id: str | None = None
    name: str | None = None
    full_name: str | None = None
    email: str | None = None
    mobile: str | None = None
    employee_code: str | None = None
    status: str | None = None
    department: Department | None = None
    company: Company | None = None
    position: Position | None = None

    #: Preserved exactly as sent, trailing whitespace and all — the raw layer
    #: records what arrived. Use `sector_conformed` for grouping (P-05).
    sector: str | None = None
    job_level_name: str | None = None
    #: Arrives as text ("10", "9"). Parsed to an integer here so that ordering
    #: by seniority is numeric: as strings, "10" sorts before "9".
    job_level_grade: int | None = None

    @field_validator("job_level_grade", mode="before")
    @classmethod
    def _grade_to_int(cls, value: Any) -> Any:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return int(stripped) if stripped.lstrip("-").isdigit() else value
        return value

    @property
    def sector_conformed(self) -> str | None:
        """Sector with surrounding whitespace removed.

        Measured on live data: 940 of 1,052 user objects carry a trailing
        space, and 28 distinct raw values collapse to 23 once trimmed. Grouping
        on the raw value would invent five sectors that do not exist — which is
        precisely P-05, the defect that split `Projects` from `Projects ` into
        two pivot rows in the workbook.
        """
        return self.sector.strip() if self.sector else None

    @property
    def has_job_level(self) -> bool:
        """False for roughly one person in ten; excluded from level breakdowns."""
        return self.job_level_name is not None


# ---------------------------------------------------------------------------
# sessions and attendance
# ---------------------------------------------------------------------------
class AttendanceRow(CrmModel):
    """One person recorded present at one session.

    `user` may be null — an attendance row can reference an `odoo_id` with no
    matching CRM user (a departed employee, an unsynced record). The row still
    counts and must never be dropped; it goes to the exception queue as
    IDENTITY_UNRESOLVED (P-07).
    """

    id: int
    user_odoo_id: str
    user: User | None = None
    attended_at: datetime | None = None

    @field_validator("user_odoo_id", mode="before")
    @classmethod
    def _stringify(cls, value: Any) -> Any:
        return str(value) if isinstance(value, int) else value

    @property
    def identity_resolvable(self) -> bool:
        return self.user is not None


class SessionRow(CrmModel):
    """One delivery of a program on one day.

    `id` is the stable session identifier — the answer to P-12. The workbook's
    `#` column was never a session key (117 values over 415 rows, 99 appearing
    once and 18 repeating), so `COUNT(DISTINCT session_key)` over it was
    meaningless. Session identity comes from here and nowhere else.
    """

    id: int
    session_date: date
    session_time_from: time | None = None
    session_time_to: time | None = None
    #: Free text, and nullable. Trainer name variants still need conforming
    #: through an alias table (P-04) — the CRM stores a string, not an entity.
    trainer_name: str | None = None
    location: Location | None = None
    attendance_count: int = 0
    attendance: list[AttendanceRow] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reject_impossible_times(self) -> SessionRow:
        """An end before its start is bad data, not a short session.

        Left unchecked it yields a negative duration, which sums into Training
        Hours Delivered and quietly reduces a published figure.
        """
        if (
            self.session_time_from is not None
            and self.session_time_to is not None
            and self.session_time_to <= self.session_time_from
        ):
            raise ValueError(
                f"session {self.id}: session_time_to {self.session_time_to} "
                f"is not after session_time_from {self.session_time_from}"
            )
        return self

    @property
    def duration_derivable(self) -> bool:
        """False when either time is missing.

        Such a session is excluded from both hour metrics and raised as a
        DURATION_UNDERIVABLE exception — counted or excepted, never neither.
        """
        return self.session_time_from is not None and self.session_time_to is not None

    @property
    def duration_hours(self) -> Decimal | None:
        """Training Hours Delivered, derived from the times rather than typed."""
        if self.session_time_from is None or self.session_time_to is None:
            return None
        started = (
            self.session_time_from.hour * 3600
            + self.session_time_from.minute * 60
            + self.session_time_from.second
        )
        ended = (
            self.session_time_to.hour * 3600
            + self.session_time_to.minute * 60
            + self.session_time_to.second
        )
        return (Decimal(ended - started) / Decimal(3600)).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# answers
# ---------------------------------------------------------------------------
class SelectedOption(CrmModel):
    id: int
    value: str | None = None


class SurveyAnswer(CrmModel):
    id: int
    question_id: int
    question_title: str | None = None
    answer_type: AnswerType | None = None
    answer: str | None = None
    selected_option: SelectedOption | None = None
    answered_at: datetime | None = None


class AssessmentAnswer(CrmModel):
    id: int
    question_id: int
    question_title: str | None = None
    answer: str | None = None
    answered_at: datetime | None = None


class SurveyOption(CrmModel):
    id: int
    value: str | None = None
    description: str | None = None


class SurveyQuestion(CrmModel):
    id: int
    title: str | None = None
    answer_type: AnswerType | None = None
    required: bool | None = None
    options: list[SurveyOption] = Field(default_factory=list)


class Survey(CrmModel):
    id: int
    title: str | None = None
    status: str | None = None
    questions: list[SurveyQuestion] = Field(default_factory=list)


class AssessmentQuestion(CrmModel):
    id: int
    title: str | None = None
    is_required: bool | None = None


class Assessment(CrmModel):
    id: int
    name: str | None = None
    questions: list[AssessmentQuestion] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# roster
# ---------------------------------------------------------------------------
class RosterEntry(CrmModel):
    """One person the program touched.

    `users[]` is the union of three groups — enrolled, walked in, or merely
    answered a survey — so `is_enrolled` must be checked before counting anyone
    as an enrollment. Treating the roster as the enrollment list would inflate
    the funnel's first step and understate No-show Rate.
    """

    user_odoo_id: str
    user: User | None = None
    is_enrolled: bool = False
    enrolled_at: datetime | None = None
    #: This person's attended sessions over the program's total sessions.
    #: A DIFFERENT formula from `statistics.attendance_rate` — see that field.
    attendance_rate: float | None = None
    survey_answers: list[SurveyAnswer] = Field(default_factory=list)
    assessment_answers: list[AssessmentAnswer] = Field(default_factory=list)

    @field_validator("user_odoo_id", mode="before")
    @classmethod
    def _stringify(cls, value: Any) -> Any:
        return str(value) if isinstance(value, int) else value

    @property
    def is_walk_in(self) -> bool:
        """Attended or answered without an enrollment row."""
        return not self.is_enrolled

    @property
    def responded_to_survey(self) -> bool:
        return bool(self.survey_answers)


class Statistics(CrmModel):
    """Whole-program counts. They do not change as you page the programs.

    Useful as a cross-check on our own aggregation: if our count of distinct
    attendees disagrees with `attended_users_count`, one of us is wrong and it
    should fail loudly rather than be reconciled by hand.
    """

    capacity: int | None = None
    sessions_count: int | None = None
    enrolled_users_count: int | None = None
    #: Distinct people who attended at least one session, walk-ins included —
    #: so this can legitimately exceed `enrolled_users_count`.
    attended_users_count: int | None = None
    enrolled_attended_users_count: int | None = None
    #: A person attending two sessions counts twice.
    attendance_records_count: int | None = None
    #: enrolled_attended / enrolled. Deliberately NOT attended/enrolled, which
    #: mixes two populations and can exceed 100% — the same class of error as
    #: P-13 in the workbook.
    attendance_rate: float | None = None
    survey_respondents_count: int | None = None
    survey_answers_count: int | None = None
    assessment_respondents_count: int | None = None
    assessment_answers_count: int | None = None


# ---------------------------------------------------------------------------
# the program tree
# ---------------------------------------------------------------------------
class Program(CrmModel):
    """A Learning Program, with everything it owns nested inside.

    Keyed on `id`, never on title — the direct fix for P-02, where two
    separately-run "Hard Talks" programs merged into one because the workbook's
    pivots grouped by title.
    """

    id: int
    title: str
    subtitle: str | None = None
    description: str | None = None

    #: The stored value. Not recomputed as sessions pass, so it can still read
    #: `upcoming` after the last session has ended.
    status: ProgramStatus | None = None
    #: Derived from the session dates. THIS is the one to use for "is it done?"
    computed_status: ProgramStatus | None = None

    capacity: int | None = Field(default=None, ge=0)
    type: ProgramType | None = None
    target: ProgramTarget | None = None

    #: Earliest / latest session date; null when the program has no sessions.
    start_date: date | None = None
    end_date: date | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    parent: ParentProgram | None = None
    track: Track | None = None
    #: Empty when `target` is `public`. Answers Q-02 — the CRM does record
    #: which department a customised program was built for.
    departments: list[Department] = Field(default_factory=list)
    companies: list[Company] = Field(default_factory=list)

    statistics: Statistics | None = None
    sessions: list[SessionRow] = Field(default_factory=list)
    users: list[RosterEntry] = Field(default_factory=list)
    survey: Survey | None = None
    assessment: Assessment | None = None

    # -- derived --------------------------------------------------------
    @property
    def counts_toward_total_programs(self) -> bool:
        """Total Programs counts completed programs, by `computed_status`."""
        return self.computed_status is ProgramStatus.COMPLETED

    @property
    def enrolled(self) -> list[RosterEntry]:
        return [entry for entry in self.users if entry.is_enrolled]

    @property
    def walk_ins(self) -> list[RosterEntry]:
        return [entry for entry in self.users if entry.is_walk_in]

    @property
    def training_hours_delivered(self) -> Decimal:
        """Catalogue hours: how much distinct training was built and delivered.

        Not Learner Hours, which multiplies by the people who received it. The
        workbook tracked both (130.5 and 1,386) and named neither.
        """
        return sum(
            (s.duration_hours for s in self.sessions if s.duration_hours is not None),
            Decimal("0"),
        )

    @property
    def sessions_missing_duration(self) -> list[SessionRow]:
        return [s for s in self.sessions if not s.duration_derivable]

    @property
    def unresolved_attendance(self) -> list[AttendanceRow]:
        """Attendance rows whose `odoo_id` matches no CRM user (P-07)."""
        return [row for s in self.sessions for row in s.attendance if not row.identity_resolvable]

    @property
    def trainer_names(self) -> set[str]:
        """Distinct trainer strings across the sessions.

        Free text, so variants still need conforming through an alias table
        before any per-trainer figure is published (P-04).
        """
        return {s.trainer_name for s in self.sessions if s.trainer_name}


class PageMeta(CrmModel):
    """The pagination block that sits beside the programs list."""

    current_page: int
    per_page: int
    total: int
    last_page: int
    #: `from`/`to` are null on an empty page. `from` is a Python keyword, so it
    #: is aliased.
    from_: int | None = Field(default=None, alias="from")
    to: int | None = None
    has_more_pages: bool = False


class ProgramsPage(CrmModel):
    """One page of the dataset endpoint's response."""

    programs: list[Program] = Field(default_factory=list)
    meta: PageMeta
