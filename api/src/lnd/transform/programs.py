"""The shredder: one nested CRM program into three fact grains.

THE SHAPE OF THE PROBLEM

The BRD assumed five flat CRM entities — programs, sessions, enrollments,
attendance, feedback — each with its own endpoint and its own raw rows. The live
Learning Program Dataset returns one tree per program with all five nested
inside it, so `raw` holds 55 program rows that have to become roughly 409
enrollments, 415 attendance rows and 77 evaluations. This module is that fan-out,
and it is the reason `raw`'s grain and `core`'s grain are allowed to differ:
`raw` records what arrived, and what arrived is a tree.

That has a consequence Person B needs to know about — `Entity.SESSION`,
`ENROLLMENT`, `ATTENDANCE` and `EVALUATION` may never be landed as separate raw
entities, so a sync catalogue expecting them will report four entities as
permanently `never_synced`.

WHAT IT WRITES, AND WHAT IT REFUSES TO WRITE

Everything is an upsert keyed on the source's own identifiers, so a re-run of
the same program produces the same rows (FR-A10) — which is what makes the whole
model replayable from `raw` after a transform fix, with no call to the CRM.

Nothing is ever dropped. An attendee who resolves to nobody still gets an
attendance row with a null `employee_key` and an IDENTITY_UNRESOLVED exception;
a session with no end time still exists, with a null duration and a
DURATION_UNDERIVABLE exception. The workbook's habit of losing such records
silently is the single defect this whole layer exists to make impossible.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from lnd.models.app_ import (
    EnrichmentField,
    ProgramOverride,
    SurveyOptionScore,
    SurveyQuestionMap,
    TrainerAlias,
)
from lnd.models.core import (
    DimProgram,
    DimSession,
    DimTrainer,
    EvaluationDimension,
    FactAttendance,
    FactEnrollment,
    FactEvaluation,
    NpsBand,
    ValueSource,
)
from lnd.models.ops import DqRule
from lnd.sources.crm.models import (
    AnswerType,
    Program,
    ProgramTarget,
    RosterEntry,
    SurveyAnswer,
    User,
)
from lnd.transform import trainer_kind
from lnd.transform.conform import normalise, trim
from lnd.transform.exceptions import ExceptionRecorder
from lnd.transform.identity import IdentityResolver, Resolution

log = logging.getLogger(__name__)

#: The conventional Net Promoter bands on a 0-10 scale. Scores from other
#: scales are rescaled to 0-10 before banding, so there is exactly one place
#: where "what counts as a promoter" is decided.
PROMOTER_FLOOR = 9
PASSIVE_FLOOR = 7

#: The scale every quality score is stored on, per `fact_evaluation`'s CHECK.
QUALITY_MIN, QUALITY_MAX = 1, 5

#: Which `EvaluationDimension` writes into which column.
SCORE_COLUMNS: dict[EvaluationDimension, str] = {
    EvaluationDimension.KNOWLEDGE_RELEVANCE: "score_knowledge_relevance",
    EvaluationDimension.ACTIVITY_EFFECTIVENESS: "score_activity_effectiveness",
    EvaluationDimension.LOGISTICS_EFFECTIVENESS: "score_logistics_effectiveness",
    EvaluationDimension.FACILITATOR_PERFORMANCE: "score_facilitator_performance",
}


@dataclass(frozen=True)
class TransformCounts:
    """What one pass wrote, and what it deliberately did not.

    Every field here is a count of *rows*, not of distinct people, because
    `invariant.py` compares these against a count of the rows the payload
    offered. `attendance` used to be the number of distinct attendees, which
    reads the same in a one-session fixture and understates every real program:
    one person at four sessions is four attendance rows.

    `attendance_duplicates` is the only route by which a fact row the source
    offered does not reach `core` — the second scan of one person at one
    session. It is reported rather than merely excepted so the invariant can
    account for it, because a skipped row that nothing counts is exactly the
    silent loss this layer exists to end.
    """

    programs: int = 0
    sessions: int = 0
    enrollments: int = 0
    attendance: int = 0
    evaluations: int = 0
    trainers_created: int = 0
    #: Distinct people, for reporting. Not part of any invariant.
    attendees: int = 0
    #: Offered by the source, refused by the grain. Excepted as
    #: DUPLICATE_ATTENDANCE.
    attendance_duplicates: int = 0


@dataclass(frozen=True)
class AttendanceOutcome:
    """What `_write_attendance` did, since it has three answers to give."""

    attendees: set[str]
    rows: int
    duplicates: int


def rescale(value: float, *, source: tuple[int, int], target: tuple[int, int]) -> int:
    """Move a score from one scale onto another, linearly.

    Needed because the recommend question is conventionally 0-10 while the
    quality questions are 1-5, and a survey is free to use either. Rescaling in
    one function means "a 5 out of 5 is a promoter" is a decision made once and
    visible, rather than an accident of whichever branch happened to run.

    Rounding is half-up via `+ 0.5`, which biases a borderline score upward.
    That is a real choice and it is the kinder one; it is stated here so that a
    reconciliation difference of one respondent has a documented cause.
    """
    source_min, source_max = source
    target_min, target_max = target
    if source_max == source_min:
        raise ValueError(f"degenerate source scale: {source}")
    ratio = (value - source_min) / (source_max - source_min)
    return int(target_min + ratio * (target_max - target_min) + 0.5)


def band_for(score_0_10: int) -> NpsBand:
    """Promoter, passive or detractor.

    Banding at write time, rather than storing a bare score, is what stops NPS
    from being computed as `AVG(score)`. The metric is
    (promoters - detractors) / responses, aggregated over the group and divided
    once — the workbook averaged a per-row plus-or-minus 1 and got a number
    that was not NPS at all (P-03).
    """
    if score_0_10 >= PROMOTER_FLOOR:
        return NpsBand.PROMOTER
    if score_0_10 >= PASSIVE_FLOOR:
        return NpsBand.PASSIVE
    return NpsBand.DETRACTOR


@dataclass
class Overlay:
    """The `app` enrichment, read once per pass.

    Read once for the same reason the identity index is: 55 programs against a
    handful of overrides is not worth a query per program, and holding the
    whole overlay makes "CRM value or override?" a dictionary lookup at the one
    point where the decision is made.
    """

    program_overrides: dict[tuple[int, EnrichmentField], str]
    #: Keyed `(survey_id, question_id)`. The survey is part of the key because
    #: a question id is only unique within its survey — the live fixtures show
    #: a survey question and an assessment question both numbered 42.
    #:
    #: The lookup half of the key is `int | None`: a program with no survey has
    #: no survey id, and that lookup must simply miss rather than be guarded at
    #: every call site.
    question_map: dict[tuple[int | None, int], SurveyQuestionMap]
    option_scores: dict[tuple[int, int], int]
    trainer_aliases: dict[str, int]

    @classmethod
    def load(cls, session: Session) -> Overlay:
        overrides = {
            (program_id, field_): value
            for program_id, field_, value in session.execute(
                select(
                    ProgramOverride.crm_program_id,
                    ProgramOverride.field,
                    ProgramOverride.value,
                ).where(ProgramOverride.superseded_at.is_(None))
            ).all()
        }
        question_map: dict[tuple[int | None, int], SurveyQuestionMap] = {
            (row.crm_survey_id, row.crm_question_id): row
            for row in session.execute(
                select(SurveyQuestionMap).where(SurveyQuestionMap.superseded_at.is_(None))
            ).scalars()
        }
        option_scores = {
            (question_id, option_id): score
            for question_id, option_id, score in session.execute(
                select(
                    SurveyOptionScore.crm_question_id,
                    SurveyOptionScore.crm_option_id,
                    SurveyOptionScore.score,
                ).where(SurveyOptionScore.superseded_at.is_(None))
            ).all()
        }
        aliases: dict[str, int] = dict(
            session.execute(
                select(TrainerAlias.normalised_name, TrainerAlias.trainer_key).where(
                    TrainerAlias.superseded_at.is_(None)
                )
            ).all()  # type: ignore[arg-type]
        )
        return cls(overrides, question_map, option_scores, aliases)


@dataclass
class TrainerRegistry:
    """Normalised trainer name to `dim_trainer.trainer_key`.

    Each distinct normalised spelling gets its own trainer row. That is
    deliberate and it is not the merge: the machine cannot know that `A. Nasr`
    and `Ahmed Nasr` are one person, and guessing would silently combine two
    real trainers' scorecards with nothing to show it happened. A person
    authors the merge in `app.trainer_alias`, which is consulted first here, so
    a merge applies to the whole history on the next pass rather than needing a
    correction.
    """

    session: Session
    aliases: dict[str, int]
    _cache: dict[str, int] = field(default_factory=dict)
    created: int = 0

    def key_for(self, raw_name: str | None) -> int | None:
        """The trainer key for a session's free-text trainer, creating if new."""
        normalised = normalise(raw_name)
        if normalised is None:
            return None

        alias = self.aliases.get(normalised)
        if alias is not None:
            return alias
        if normalised in self._cache:
            return self._cache[normalised]

        canonical = trim(raw_name)
        assert canonical is not None  # normalise() returned non-None, so trim() does too

        # ON CONFLICT DO NOTHING then re-select, rather than a read-then-write:
        # two overlapping passes seeing the same new trainer would otherwise
        # both insert and one would fail the unique constraint, taking an
        # otherwise good transform down with it.
        placeholder, external = trainer_kind.classify(canonical)
        self.session.execute(
            insert(DimTrainer)
            .values(
                canonical_name=canonical,
                normalised_name=normalised,
                is_placeholder=placeholder,
                is_external=external,
            )
            # Set on conflict as well as on insert. The classification is
            # derived, so a row created before the list knew about a name must
            # pick the answer up on the next pass rather than keep the old one
            # until somebody drops `core`.
            .on_conflict_do_update(
                constraint="uq_dim_trainer_canonical_name",
                set_={"is_placeholder": placeholder, "is_external": external},
            )
        )
        trainer_key = self.session.execute(
            select(DimTrainer.trainer_key).where(DimTrainer.canonical_name == canonical)
        ).scalar_one()

        self._cache[normalised] = trainer_key
        self.created += 1
        return trainer_key


def collect_users(program: Program) -> dict[str, User]:
    """Every person this program mentions, deduplicated by `odoo_id`.

    Both the roster and the attendance rows carry nested `user` objects, and
    the same person appears in both. The dimension is loaded from the union
    before any fact is written, because resolving an attendee against a
    dimension that does not yet contain them would quarantine everyone who
    first appears in this pass.
    """
    users: dict[str, User] = {}
    for entry in program.users:
        if entry.user is not None:
            users[entry.user_odoo_id] = entry.user
    for session_row in program.sessions:
        for attendance in session_row.attendance:
            if attendance.user is not None:
                users.setdefault(attendance.user_odoo_id, attendance.user)
    return users


def program_dates(program: Program) -> list[date]:
    """Every date this program will reference, for the calendar to cover."""
    dates: list[date] = [s.session_date for s in program.sessions]
    dates.extend(d for d in (program.start_date, program.end_date) if d is not None)
    for entry in program.users:
        if entry.enrolled_at is not None:
            dates.append(entry.enrolled_at.date())
        for answer in entry.survey_answers:
            if answer.answered_at is not None:
                dates.append(answer.answered_at.date())
    return dates


@dataclass
class ProgramTransformer:
    """Transforms one program at a time, against a pass-wide context."""

    session: Session
    resolver: IdentityResolver
    overlay: Overlay
    trainers: TrainerRegistry
    exceptions: ExceptionRecorder

    def transform(self, program: Program, *, raw_record_id: int | None = None) -> TransformCounts:
        """One program to its dimension row and its three fact grains."""
        now = datetime.now(UTC)

        self._write_program(program, now=now, raw_record_id=raw_record_id)
        sessions = self._write_sessions(program, now=now, raw_record_id=raw_record_id)
        enrolled_ids = self._write_enrollments(program, now=now, raw_record_id=raw_record_id)
        attendance = self._write_attendance(
            program,
            sessions=sessions,
            enrolled_ids=enrolled_ids,
            now=now,
            raw_record_id=raw_record_id,
        )
        evaluations = self._write_evaluations(
            program, attended_ids=attendance.attendees, now=now, raw_record_id=raw_record_id
        )

        return TransformCounts(
            programs=1,
            sessions=len(sessions),
            enrollments=len(enrolled_ids),
            attendance=attendance.rows,
            evaluations=evaluations,
            attendees=len(attendance.attendees),
            attendance_duplicates=attendance.duplicates,
        )

    # -- dimension ---------------------------------------------------------
    def _write_program(self, program: Program, *, now: datetime, raw_record_id: int | None) -> None:
        department, department_source = self._resolve_department(program)
        trainer_key, trainer_source = self._resolve_trainer(program)

        values = {
            "crm_program_id": program.id,
            "title": trim(program.title) or program.title,
            "subtitle": trim(program.subtitle),
            "description": program.description,
            "status": program.status,
            "computed_status": program.computed_status,
            "type": program.type,
            "target": program.target,
            "capacity": program.capacity,
            "start_date": program.start_date,
            "end_date": program.end_date,
            "track_id": program.track.id if program.track else None,
            "track_title": trim(program.track.title) if program.track else None,
            "parent_program_id": program.parent.id if program.parent else None,
            "customised_department_name": department,
            "customised_department_source": department_source,
            "trainer_key": trainer_key,
            "trainer_source": trainer_source,
            # A program that reappears at source is no longer deleted. Clearing
            # this on every pass is what makes the nightly reconcile's soft
            # delete reversible without anyone intervening.
            "deleted_at_source": None,
            "raw_record_id": raw_record_id,
            "transformed_at": now,
        }
        self._upsert(DimProgram, values, index_elements=["crm_program_id"])

        # A customised program with no department is excluded from every
        # department breakdown while still counting in the totals — so it is
        # COUNTED, not quarantined, and the exception says which breakdown it
        # is missing from rather than implying the program is lost.
        if program.target is ProgramTarget.DEPARTMENT and department is None:
            self.exceptions.raise_for(
                DqRule.CUSTOMISED_DEPT_MISSING,
                key_parts=(program.id,),
                summary=(
                    f"Program {program.id} ({program.title}) targets a department but "
                    "none is assigned; it is excluded from department breakdowns."
                ),
                crm_program_id=program.id,
            )

        if trainer_key is None:
            self.exceptions.raise_for(
                DqRule.TRAINER_MISSING,
                key_parts=(program.id,),
                summary=(
                    f"Program {program.id} ({program.title}) has no trainer on any session "
                    "and no override; it is excluded from trainer scorecards."
                ),
                crm_program_id=program.id,
            )

        enrolled = len(program.enrolled)
        if program.capacity is not None and enrolled > program.capacity:
            self.exceptions.raise_for(
                DqRule.CAPACITY_EXCEEDED,
                key_parts=(program.id,),
                summary=(
                    f"Program {program.id} has {enrolled} enrollments against a "
                    f"capacity of {program.capacity}; reported as-is."
                ),
                crm_program_id=program.id,
                details={"enrolled": enrolled, "capacity": program.capacity},
            )

    def _resolve_department(self, program: Program) -> tuple[str | None, ValueSource]:
        """CRM first, override second — FR-C04 in three lines.

        The order is what makes an override retire by itself. When the CRM
        started supplying `departments[]` (Q-02, answered), every hand-entered
        department became redundant with no migration and no data fix; the
        column just stopped reading them.
        """
        if program.departments:
            name = trim(program.departments[0].name)
            if name:
                return name, ValueSource.CRM

        override = self.overlay.program_overrides.get(
            (program.id, EnrichmentField.CUSTOMISED_DEPARTMENT)
        )
        if override:
            return trim(override), ValueSource.OVERRIDE

        return None, ValueSource.MISSING

    def _resolve_trainer(self, program: Program) -> tuple[int | None, ValueSource]:
        """The program-level trainer: the sessions' if they agree, else the override.

        "If they agree" is doing real work. A program whose sessions name two
        different trainers has no single program-level trainer, and picking the
        first would attribute the whole program's NPS to one of them. It falls
        through to the override, and if there is none, to TRAINER_MISSING —
        visible rather than wrong.
        """
        session_keys = {
            key
            for key in (self.trainers.key_for(s.trainer_name) for s in program.sessions)
            if key is not None
        }
        if len(session_keys) == 1:
            return session_keys.pop(), ValueSource.CRM

        override = self.overlay.program_overrides.get((program.id, EnrichmentField.TRAINER_NAME))
        if override:
            key = self.trainers.key_for(override)
            if key is not None:
                return key, ValueSource.OVERRIDE

        return None, ValueSource.MISSING

    def _write_sessions(
        self, program: Program, *, now: datetime, raw_record_id: int | None
    ) -> dict[int, dict[str, Any]]:
        """One row per session, and DURATION_UNDERIVABLE for each that lacks times.

        Returns the values written, keyed by session id, so that attendance can
        carry each session's duration onto its attendees without reading back
        what it has just written.
        """
        written: dict[int, dict[str, Any]] = {}

        for row in program.sessions:
            duration = row.duration_hours
            values = {
                "crm_session_id": row.id,
                "crm_program_id": program.id,
                "session_date": row.session_date,
                "session_time_from": row.session_time_from,
                "session_time_to": row.session_time_to,
                "duration_hours": duration,
                # The CHECK on this table makes the pair total, so this is not
                # a second source of truth — it is the same fact, indexed.
                "duration_derivable": duration is not None,
                "trainer_key": self.trainers.key_for(row.trainer_name),
                "trainer_name_raw": trim(row.trainer_name),
                "location_name": trim(row.location.name) if row.location else None,
                "deleted_at_source": None,
                "raw_record_id": raw_record_id,
                "transformed_at": now,
            }
            self._upsert(DimSession, values, index_elements=["crm_session_id"])
            written[row.id] = values

            if duration is None:
                self.exceptions.raise_for(
                    DqRule.DURATION_UNDERIVABLE,
                    key_parts=(program.id, row.id),
                    summary=(
                        f"Session {row.id} of program {program.id} is missing a start or "
                        "end time; it is excluded from Training Hours Delivered and "
                        "its attendees contribute no Learner Hours."
                    ),
                    crm_program_id=program.id,
                    crm_session_id=row.id,
                    details={
                        "session_time_from": str(row.session_time_from),
                        "session_time_to": str(row.session_time_to),
                    },
                )

        return written

    # -- facts -------------------------------------------------------------
    def _write_enrollments(
        self, program: Program, *, now: datetime, raw_record_id: int | None
    ) -> set[str]:
        """One row per genuinely enrolled person. Returns their `odoo_id`s.

        `is_enrolled` is checked rather than taking `users[]` wholesale: that
        list is the union of enrolled people, walk-ins and survey respondents,
        and treating it as the enrollment list would inflate the funnel's first
        step and understate No-show Rate.
        """
        enrolled: set[str] = set()

        for entry in program.enrolled:
            resolution = self._resolve(entry, program)
            enrolled.add(entry.user_odoo_id)

            self._upsert(
                FactEnrollment,
                {
                    "crm_program_id": program.id,
                    "employee_odoo_id": entry.user_odoo_id,
                    "employee_key": resolution.employee_key,
                    "identity_status": resolution.status,
                    "enrolled_at": entry.enrolled_at,
                    "enrolled_date": entry.enrolled_at.date() if entry.enrolled_at else None,
                    "deleted_at_source": None,
                    "raw_record_id": raw_record_id,
                    "transformed_at": now,
                },
                index_elements=["crm_program_id", "employee_odoo_id"],
            )

        return enrolled

    def _write_attendance(
        self,
        program: Program,
        *,
        sessions: dict[int, dict[str, Any]],
        enrolled_ids: set[str],
        now: datetime,
        raw_record_id: int | None,
    ) -> AttendanceOutcome:
        """One row per (session, person), plus what was refused and why.

        `learning_hours` is the session's duration carried onto each attendee.
        Summed it gives Learner Hours; summing `dim_session.duration_hours`
        gives Training Hours Delivered. The workbook tracked both, at 1,386 and
        130.5, and named neither.
        """
        attendees: set[str] = set()
        seen_in_session: set[tuple[int, str]] = set()
        rows = 0
        duplicates = 0

        # Everyone the program says it touched. The roster is the union of
        # enrolled people, walk-ins and survey respondents, so absence from it
        # is not "did not enrol" — it is "the CRM does not know this person was
        # here", and it is the only reason the payload gives us an attendance
        # row with no `user` object behind it.
        roster_ids = {entry.user_odoo_id for entry in program.users}

        for session_row in program.sessions:
            stored = sessions.get(session_row.id)
            if stored is None:  # pragma: no cover - every session is written above
                continue
            hours = stored["duration_hours"]

            for row in session_row.attendance:
                pair = (session_row.id, row.user_odoo_id)
                if pair in seen_in_session:
                    # The database would refuse the second row anyway; catching
                    # it here is what turns a constraint violation into a named,
                    # actionable exception instead of a failed pass.
                    self.exceptions.raise_for(
                        DqRule.DUPLICATE_ATTENDANCE,
                        key_parts=(session_row.id, row.user_odoo_id),
                        summary=(
                            f"Employee {row.user_odoo_id} was scanned more than once for "
                            f"session {session_row.id}; counted once."
                        ),
                        crm_program_id=program.id,
                        crm_session_id=session_row.id,
                        employee_odoo_id=row.user_odoo_id,
                        details={"duplicate_attendance_id": row.id},
                    )
                    duplicates += 1
                    continue
                seen_in_session.add(pair)
                attendees.add(row.user_odoo_id)

                resolution = self.resolver.resolve(
                    odoo_id=row.user_odoo_id,
                    employee_code=row.user.employee_code if row.user else None,
                    email=row.user.email if row.user else None,
                    full_name=(row.user.full_name or row.user.name) if row.user else None,
                )
                self._quarantine_if_unresolved(resolution, program, row.user_odoo_id)

                self._upsert(
                    FactAttendance,
                    {
                        "crm_session_id": session_row.id,
                        "crm_program_id": program.id,
                        "crm_attendance_id": row.id,
                        "employee_odoo_id": row.user_odoo_id,
                        "employee_key": resolution.employee_key,
                        "identity_status": resolution.status,
                        "attended_at": row.attended_at,
                        # The session's date, not the scan's: a scan recorded
                        # at one minute past midnight belongs to the session it
                        # was for, not to the next day's figures.
                        "attended_date": session_row.session_date,
                        "learning_hours": hours,
                        "deleted_at_source": None,
                        "raw_record_id": raw_record_id,
                        "transformed_at": now,
                    },
                    index_elements=["crm_session_id", "employee_odoo_id"],
                )
                rows += 1

                # Keyed on the person, not on (person, session): somebody
                # missing from the roster is missing from it once, however many
                # sessions they attended, and it is one roster correction to
                # make. Raised before the enrollment check because it is the
                # stronger statement — a person outside the roster is
                # necessarily outside the enrollment list too, and reporting
                # both would put one correction in the queue twice.
                if row.user_odoo_id not in roster_ids:
                    self.exceptions.raise_for(
                        DqRule.ATTENDEE_OUTSIDE_ROSTER,
                        key_parts=(program.id, row.user_odoo_id),
                        summary=(
                            f"Employee {row.user_odoo_id} attended program {program.id} "
                            "but appears nowhere in its roster, so no sector, department "
                            "or job level is known for them; counted in attendance and "
                            "Learner Hours, excluded from every coverage breakdown."
                        ),
                        crm_program_id=program.id,
                        crm_session_id=session_row.id,
                        employee_odoo_id=row.user_odoo_id,
                        details={"roster_size": len(roster_ids)},
                    )
                elif row.user_odoo_id not in enrolled_ids:
                    self.exceptions.raise_for(
                        DqRule.ATTENDANCE_NO_ENROLLMENT,
                        key_parts=(program.id, row.user_odoo_id),
                        summary=(
                            f"Employee {row.user_odoo_id} attended program {program.id} "
                            "with no enrollment record; counted in attendance, excluded "
                            "from the funnel. Likely a walk-in."
                        ),
                        crm_program_id=program.id,
                        employee_odoo_id=row.user_odoo_id,
                    )

        return AttendanceOutcome(attendees=attendees, rows=rows, duplicates=duplicates)

    def _write_evaluations(
        self,
        program: Program,
        *,
        attended_ids: set[str],
        now: datetime,
        raw_record_id: int | None,
    ) -> int:
        """One row per respondent per program, with scores attributed by mapping."""
        survey_id = program.survey.id if program.survey else None
        written = 0

        for entry in program.users:
            if not entry.survey_answers:
                continue

            resolution = self._resolve(entry, program)
            scores, recommend, comments, unmapped = self._score_answers(
                entry, program=program, survey_id=survey_id
            )

            band = band_for(recommend) if recommend is not None else None
            responded_at = self._responded_at(entry)

            self._upsert(
                FactEvaluation,
                {
                    "crm_program_id": program.id,
                    "crm_survey_id": survey_id,
                    "employee_odoo_id": entry.user_odoo_id,
                    "employee_key": resolution.employee_key,
                    "identity_status": resolution.status,
                    "responded_at": responded_at,
                    "responded_date": responded_at.date() if responded_at else None,
                    **{column: scores.get(column) for column in SCORE_COLUMNS.values()},
                    "recommend_score": recommend,
                    "nps_band": band,
                    "comment": "\n\n".join(comments) or None,
                    "unmapped_question_count": unmapped,
                    "deleted_at_source": None,
                    "raw_record_id": raw_record_id,
                    "transformed_at": now,
                },
                index_elements=["crm_program_id", "employee_odoo_id"],
            )
            written += 1

            # Feedback from somebody with no attendance record indicates an
            # attendance capture failure, not a bad evaluation — the response
            # counts, and the exception points at the missing scan.
            if entry.user_odoo_id not in attended_ids:
                self.exceptions.raise_for(
                    DqRule.EVALUATION_NO_ATTENDANCE,
                    key_parts=(program.id, entry.user_odoo_id),
                    summary=(
                        f"Employee {entry.user_odoo_id} submitted feedback for program "
                        f"{program.id} with no attendance record; indicates an "
                        "attendance capture failure."
                    ),
                    crm_program_id=program.id,
                    employee_odoo_id=entry.user_odoo_id,
                )

        return written

    # -- scoring -----------------------------------------------------------
    def _score_answers(
        self, entry: RosterEntry, *, program: Program, survey_id: int | None
    ) -> tuple[dict[str, int], int | None, list[str], int]:
        """Turn one respondent's answers into the five measures.

        Every step here can fail to produce a number, and each failure raises
        its own exception rather than defaulting to something. A survey answer
        that quietly scored zero would move a published quality percentage with
        nothing to show that it had — which is precisely the class of defect
        this platform exists to end.
        """
        scores: dict[str, int] = {}
        recommend: int | None = None
        comments: list[str] = []
        unmapped = 0

        for answer in entry.survey_answers:
            mapping = self.overlay.question_map.get((survey_id, answer.question_id))

            if mapping is None:
                # A free-text answer needs no mapping — it is a comment, and
                # the scorecard prints it. Only a *scored* question that cannot
                # be attributed to a metric is a loss.
                if answer.answer_type is AnswerType.TEXT:
                    text = trim(answer.answer)
                    if text:
                        comments.append(text)
                    continue

                unmapped += 1
                self.exceptions.raise_for(
                    DqRule.SURVEY_QUESTION_UNMAPPED,
                    key_parts=(survey_id, answer.question_id),
                    summary=(
                        f"Survey question {answer.question_id} "
                        f"({answer.question_title}) has no mapping to a measured "
                        "dimension; its answers are excluded from the quality metrics "
                        "and NPS."
                    ),
                    crm_program_id=program.id,
                    details={
                        "crm_survey_id": survey_id,
                        "question_title": answer.question_title,
                        "answer_type": str(answer.answer_type),
                    },
                )
                continue

            raw_score = self._numeric_answer(answer, program=program)
            if raw_score is None:
                unmapped += 1
                continue

            source_scale = (mapping.scale_min, mapping.scale_max)
            if mapping.dimension is EvaluationDimension.RECOMMEND:
                recommend = rescale(raw_score, source=source_scale, target=(0, 10))
            else:
                scores[SCORE_COLUMNS[mapping.dimension]] = rescale(
                    raw_score, source=source_scale, target=(QUALITY_MIN, QUALITY_MAX)
                )

        return scores, recommend, comments, unmapped

    def _numeric_answer(self, answer: SurveyAnswer, *, program: Program) -> float | None:
        """The number behind an answer, or None with an exception raised.

        A `rating` answer arrives numeric. A `select` answer arrives as the
        option's *text* — "Very useful" — and which words mean four is a
        judgement about one survey's wording, so it is authored in
        `app.survey_option_score` rather than inferred from a lexicon that
        would be right until a survey offered "Somewhat useful" as its top
        option.
        """
        selected = answer.selected_option
        question_id = answer.question_id

        if selected is not None:
            score = self.overlay.option_scores.get((question_id, selected.id))
            if score is None:
                self.exceptions.raise_for(
                    DqRule.SURVEY_OPTION_UNSCORED,
                    key_parts=(question_id, selected.id),
                    summary=(
                        f"Option {selected.id} ({selected.value!r}) of question "
                        f"{question_id} has no numeric score; answers selecting it are "
                        "excluded from the quality metrics."
                    ),
                    crm_program_id=program.id,
                    details={"option_value": selected.value},
                )
                return None
            return float(score)

        text = trim(answer.answer)
        if text is None:
            return None
        try:
            return float(text)
        except ValueError:
            self.exceptions.raise_for(
                DqRule.SURVEY_OPTION_UNSCORED,
                key_parts=(question_id, text),
                summary=(
                    f"Question {question_id} is mapped to a scored dimension but "
                    f"answered with non-numeric text {text!r}; excluded."
                ),
                crm_program_id=program.id,
            )
            return None

    @staticmethod
    def _responded_at(entry: RosterEntry) -> datetime | None:
        """When the response was submitted: the earliest answer's timestamp.

        Earliest rather than latest, because a respondent who returns to amend
        one answer a week later responded on the first day, and dating the
        response to the amendment would move it into a month whose figures were
        already published.
        """
        stamps = [a.answered_at for a in entry.survey_answers if a.answered_at is not None]
        return min(stamps) if stamps else None

    # -- shared ------------------------------------------------------------
    def _resolve(self, entry: RosterEntry, program: Program) -> Resolution:
        user = entry.user
        resolution = self.resolver.resolve(
            odoo_id=entry.user_odoo_id,
            employee_code=user.employee_code if user else None,
            email=user.email if user else None,
            full_name=(user.full_name or user.name) if user else None,
        )
        self._quarantine_if_unresolved(resolution, program, entry.user_odoo_id)
        return resolution

    def _quarantine_if_unresolved(
        self, resolution: Resolution, program: Program, odoo_id: str
    ) -> None:
        """FR-B05: never drop, always register.

        Keyed on the person rather than on (person, program), so somebody who
        appears in six programs is one item in the queue and one mapping to
        author — not six of each.
        """
        if resolution.resolved:
            return
        self.exceptions.raise_for(
            DqRule.IDENTITY_UNRESOLVED,
            key_parts=(odoo_id,),
            summary=(
                f"Attendee {odoo_id} matches no employee by id, code, email or name; "
                "their records are counted in volume but excluded from every "
                "breakdown by person until mapped."
            ),
            crm_program_id=program.id,
            employee_odoo_id=odoo_id,
        )

    def _upsert(self, model: type, values: dict[str, Any], *, index_elements: list[str]) -> None:
        """Insert, or overwrite everything but the key.

        Overwriting rather than merging is what makes `core` a *pure function*
        of raw plus enrichment: a value the source stopped sending has to
        become null here, and a merge would leave yesterday's value in place
        with nothing to show it was stale.
        """
        statement = insert(model).values(values)
        updatable = {
            column: statement.excluded[column] for column in values if column not in index_elements
        }
        self.session.execute(
            statement.on_conflict_do_update(index_elements=index_elements, set_=updatable)
        )
