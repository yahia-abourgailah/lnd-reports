"""The twenty-one metrics.

Three unchanged, five restated or renamed, six corrected, seven new. Each is one
declaration: what it means, what it counts over, which filters it honours, and
how it relates to the figure the workbook published.

Nine of these currently live inside nine unauditable GETPIVOTDATA strings, and
six of those are wrong. This file is the single place any of them is defined —
the API, the exports and the golden-value tests all read it, so there is no
second definition available to disagree with the first.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Date, distinct, func, literal, select
from sqlalchemy.orm import Session

from lnd.metrics import population as pop
from lnd.metrics import scope
from lnd.metrics.base import (
    MetricSpec,
    MetricValue,
    Provenance,
    Ratio,
    Unit,
)
from lnd.metrics.filters import Dimension, MetricFilters
from lnd.models.core import NpsBand

# Which filters most fact-grain metrics accept. Program-level metrics narrow
# this: a program has no sector, so filtering programs by sector would silently
# mean "programs somebody from that sector attended", which is a different
# question wearing the same label.
LEARNER_DIMENSIONS = frozenset(
    {
        Dimension.PERIOD,
        Dimension.SECTOR,
        Dimension.DEPARTMENT,
        Dimension.COMPANY,
        Dimension.JOB_LEVEL,
        Dimension.PROGRAM,
        Dimension.PROGRAM_TYPE,
        Dimension.PROGRAM_TARGET,
    }
)
#: Attendance-grain metrics can honour a trainer as well, because an attendance
#: joins to its session and a session names its trainer — `scope.attendances`
#: implements it. Deliberately not extended to the evaluation- or
#: enrollment-grain metrics: a survey response belongs to a programme, not to a
#: session, and an enrollment names no trainer at all. Narrowing Survey Response
#: Rate by trainer would filter its denominator and not its numerator, which is
#: the shape of P-13 and would read as a plausible number.
ATTENDANCE_DIMENSIONS = LEARNER_DIMENSIONS | {Dimension.TRAINER}

#: Metrics a learner profile is built from: the ones whose grain records what a
#: person did, and whose numerator and denominator both narrow to that person.
#:
#: Deliberately not Participation Rate, Coverage Gap or any programme-grain
#: metric. One person's participation rate is 1/1, which is not a rate; a
#: session is not attributable to one attendee, so "their Training Days" would
#: silently mean the days somebody else also delivered to. Those metrics refuse
#: the dimension, and a profile shows what it can honestly show.
PROFILE_DIMENSIONS = LEARNER_DIMENSIONS | {Dimension.LEARNER}
PROFILE_ATTENDANCE_DIMENSIONS = ATTENDANCE_DIMENSIONS | {Dimension.LEARNER}

PROGRAM_DIMENSIONS = frozenset(
    {
        Dimension.PERIOD,
        Dimension.PROGRAM,
        Dimension.PROGRAM_TYPE,
        Dimension.PROGRAM_TARGET,
        Dimension.TRAINER,
    }
)


def _population_is_estimated(
    session: Session, spec: MetricSpec, filters: MetricFilters
) -> bool:
    """Whether this metric rests on employee rows the platform had to assume.

    Derived from the metric's declared population rather than remembered by each
    metric, because it was forgotten once already: Participation Rate returned
    the flag and Coverage Gap — the same headcount, minus attendance — returned
    nothing, so a figure resting on assumed employment presented itself as
    exact.

    The assumption is real and unavoidable. The CRM gives no hire date, so
    somebody it first showed us in September is recorded as valid from before
    the platform existed and counts in August's headcount. `is_estimated` is how
    a reader is told that, and any metric over this population owes it.
    """
    if spec.population is not pop.ENROLLABLE_EMPLOYEES:
        return False
    eligible = scope.enrollable_employees(filters.without(Dimension.PERIOD)).subquery()
    return bool(
        session.scalar(
            select(func.count()).select_from(eligible).where(eligible.c.is_estimated)
        )
    )


def _value(
    spec: MetricSpec,
    filters: MetricFilters,
    *,
    value: Decimal | None,
    sample_size: int,
    numerator: Decimal | None = None,
    denominator: Decimal | None = None,
    is_estimated: bool = False,
) -> MetricValue:
    return MetricValue(
        key=spec.key,
        title=spec.title,
        definition=spec.definition,
        population=spec.population,
        provenance=spec.provenance,
        unit=spec.unit,
        value=value,
        numerator=numerator,
        denominator=denominator,
        sample_size=sample_size,
        filters_applied=filters.describe(),
        dimensions_filtered=filters.dimensions_used,
        is_estimated=is_estimated,
    )


@dataclass(frozen=True)
class ScalarMetric:
    """A count or a sum. One number, and the rows behind it."""

    spec: MetricSpec

    @property
    def key(self) -> str:
        return self.spec.key

    def compute(self, session: Session, filters: MetricFilters) -> MetricValue:
        self.spec.reject_unsupported(filters)
        total, rows = self._measure(session, filters)
        return _value(
            self.spec,
            filters,
            value=total,
            sample_size=rows,
            is_estimated=_population_is_estimated(session, self.spec, filters),
        )

    def _measure(
        self, session: Session, filters: MetricFilters
    ) -> tuple[Decimal | None, int]:  # pragma: no cover - overridden
        raise NotImplementedError


@dataclass(frozen=True)
class RatioMetric:
    """Numerator and denominator, divided last.

    Both terms are returned, so a breakdown re-aggregates by summing them rather
    than by averaging the ratios — which is right whatever the group sizes are,
    and wrong the moment they differ.
    """

    spec: MetricSpec
    #: Multiplied into the value. 100 turns a proportion into a percentage; NPS
    #: uses the same 100 because it is a proportion on a -1..+1 range.
    factor: Decimal = Decimal(100)

    @property
    def key(self) -> str:
        return self.spec.key

    def compute(self, session: Session, filters: MetricFilters) -> MetricValue:
        self.spec.reject_unsupported(filters)
        ratio, sample_size, is_estimated = self._measure(session, filters)
        raw = ratio.value
        return _value(
            self.spec,
            filters,
            value=None if raw is None else raw * self.factor,
            numerator=ratio.numerator,
            denominator=ratio.denominator,
            sample_size=sample_size,
            is_estimated=is_estimated,
        )

    def _measure(
        self, session: Session, filters: MetricFilters
    ) -> tuple[Ratio, int, bool]:  # pragma: no cover - overridden
        raise NotImplementedError


# ---------------------------------------------------------------------------
# unchanged — same definition, same number
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TotalParticipants(ScalarMetric):
    def _measure(self, session: Session, filters: MetricFilters) -> tuple[Decimal | None, int]:
        rows = scope.attendances(filters).subquery()
        people = session.scalar(select(func.count(distinct(rows.c.employee_key)))) or 0
        total_rows = session.scalar(select(func.count()).select_from(rows)) or 0
        return Decimal(people), int(total_rows)


TOTAL_PARTICIPANTS = TotalParticipants(
    spec=MetricSpec(
        key="total_participants",
        title="Total Participants",
        definition="Distinct people with at least one attendance in scope.",
        population=pop.ATTENDANCES,
        provenance=Provenance.UNCHANGED,
        unit=Unit.COUNT,
        supports=PROFILE_ATTENDANCE_DIMENSIONS,
    )
)


@dataclass(frozen=True)
class LearnerHours(ScalarMetric):
    def _measure(self, session: Session, filters: MetricFilters) -> tuple[Decimal | None, int]:
        rows = scope.attendances(filters).subquery()
        total = session.scalar(select(func.coalesce(func.sum(rows.c.learning_hours), 0)))
        counted = session.scalar(select(func.count()).select_from(rows)) or 0
        return Decimal(total or 0), int(counted)


LEARNER_HOURS = LearnerHours(
    spec=MetricSpec(
        key="learner_hours",
        title="Learner Hours",
        definition="Hours consumed by learners: the session duration, summed once per attendance.",
        population=pop.ATTENDANCES,
        provenance=Provenance.UNCHANGED,
        unit=Unit.HOURS,
        supports=PROFILE_ATTENDANCE_DIMENSIONS,
    )
)


# ---------------------------------------------------------------------------
# quality scores — one shape, five instances
# ---------------------------------------------------------------------------
QUALITY_DEFINITION = (
    "Share of responses rating this {label} 4 or better on the 1-5 scale, "
    "over every response in scope that answered the question."
)


@dataclass(frozen=True)
class QualityScore(RatioMetric):
    """`COUNT(rating >= 4) / COUNT(responses)` for one survey dimension.

    The three corrected ones changed scope, not formula. The workbook computed
    them over a silently filtered 55 of 77 responses (P-03); these run over
    every response in the population, and return the response count beside the
    score so a shrinking denominator is visible rather than inferred.
    """

    column: str = "score_knowledge_relevance"
    threshold: int = 4

    def _measure(self, session: Session, filters: MetricFilters) -> tuple[Ratio, int, bool]:
        rows = scope.evaluations(filters).subquery()
        score = rows.c[self.column]
        answered = func.count(score)
        good = func.count().filter(score >= self.threshold)
        numerator, denominator = session.execute(select(good, answered).select_from(rows)).one()
        return (
            Ratio(Decimal(numerator or 0), Decimal(denominator or 0)),
            int(denominator or 0),
            False,
        )


def _quality(
    key: str, title: str, column: str, label: str, provenance: Provenance, note: str = ""
) -> QualityScore:
    return QualityScore(
        spec=MetricSpec(
            key=key,
            title=title,
            definition=QUALITY_DEFINITION.format(label=label),
            population=pop.RATED_EVALUATIONS,
            provenance=provenance,
            unit=Unit.PERCENT,
            supports=PROFILE_DIMENSIONS,
            note=note,
        ),
        column=column,
    )


SCOPE_NOTE = (
    "Formula unchanged. The workbook computed it over a silently filtered 55 of "
    "77 responses; this runs over every response in the declared population (P-03)."
)


KNOWLEDGE_RELEVANCE = _quality(
    "knowledge_relevance",
    "Knowledge Relevance",
    "score_knowledge_relevance",
    "knowledge as relevant",
    Provenance.CORRECTED,
    SCOPE_NOTE,
)
ACTIVITY_EFFECTIVENESS = _quality(
    "activity_effectiveness",
    "Activity Effectiveness",
    "score_activity_effectiveness",
    "activity as effective",
    Provenance.CORRECTED,
    SCOPE_NOTE,
)
LOGISTICS_EFFECTIVENESS = _quality(
    "logistics_effectiveness",
    "Logistics Effectiveness",
    "score_logistics_effectiveness",
    "logistics as effective",
    Provenance.CORRECTED,
    SCOPE_NOTE,
)
#: Unchanged, and worth saying why: it lands on 100.0% under either scope, so
#: the correction that moved the other three leaves this one where it was.
FACILITATOR_PERFORMANCE = _quality(
    "facilitator_performance",
    "Facilitator Performance",
    "score_facilitator_performance",
    # The template already appends "4 or better"; carrying it in the label too
    # rendered "rating this facilitator 4 or better 4 or better on the 1-5
    # scale" into every tooltip and every export stamp.
    "facilitator",
    Provenance.UNCHANGED,
)


@dataclass(frozen=True)
class NetPromoterScore(RatioMetric):
    """`(promoters - detractors) / responses`, on the -100..+100 scale.

    Two corrections, and only one of them is arithmetic.

    Scope is P-03: the published figure ran over 55 of 77 responses. This runs
    over every response that answered the recommend question.

    Unit is not a bug at all — the workbook reported "92.7%", which is not what
    NPS is. Real NPS runs -100 to +100. Shown side by side without saying so,
    +88 reads as a fall from 92.7, and it is not: nothing about satisfaction
    changed, the earlier figure was a different calculation on a different
    scale. `Unit.NPS` is what stops the two being formatted alike.
    """

    def _measure(self, session: Session, filters: MetricFilters) -> tuple[Ratio, int, bool]:
        rows = scope.evaluations(filters).subquery()
        band = rows.c.nps_band
        promoted, detracted, answered = session.execute(
            select(
                func.count().filter(band == NpsBand.PROMOTER.value),
                func.count().filter(band == NpsBand.DETRACTOR.value),
                func.count(band),
            ).select_from(rows)
        ).one()
        return (
            Ratio(Decimal((promoted or 0) - (detracted or 0)), Decimal(answered or 0)),
            int(answered or 0),
            False,
        )


NPS = NetPromoterScore(
    spec=MetricSpec(
        key="nps",
        title="Net Promoter Score",
        definition=(
            "(promoters - detractors) / responses, on the standard -100 to +100 scale. "
            "Promoters score 9-10, passives 7-8, detractors 0-6."
        ),
        population=pop.RATED_EVALUATIONS,
        provenance=Provenance.CORRECTED,
        unit=Unit.NPS,
        supports=PROFILE_DIMENSIONS,
        note=(
            "Two changes. Scope: over every response rather than a filtered 55 of 77 "
            "(P-03). Unit: the workbook's 92.7% was a percentage; NPS is a -100..+100 "
            "index. The two are not comparable and must not be shown as if they were."
        ),
    )
)


# ---------------------------------------------------------------------------
# restated and renamed — same intent, better-defined computation
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TrainingDays(ScalarMetric):
    def _measure(self, session: Session, filters: MetricFilters) -> tuple[Decimal | None, int]:
        rows = scope.sessions(filters).subquery()
        total = session.scalar(select(func.count(distinct(rows.c.crm_session_id)))) or 0
        return Decimal(total), int(total)


TRAINING_DAYS = TrainingDays(
    spec=MetricSpec(
        key="training_days",
        title="Training Days",
        definition="Distinct delivered sessions. One session is one training day.",
        population=pop.DELIVERED_SESSIONS,
        provenance=Provenance.RESTATED,
        unit=Unit.COUNT,
        supports=PROGRAM_DIMENSIONS,
        note=(
            "Counted on the CRM session id. The workbook counted its own '#' column, "
            "which was a row counter as well as an identifier, so a distinct count "
            "over it meant nothing (P-12)."
        ),
    )
)


@dataclass(frozen=True)
class TrainingHoursDelivered(ScalarMetric):
    def _measure(self, session: Session, filters: MetricFilters) -> tuple[Decimal | None, int]:
        rows = scope.sessions(filters).subquery()
        total = session.scalar(select(func.coalesce(func.sum(rows.c.duration_hours), 0)))
        counted = session.scalar(select(func.count()).select_from(rows)) or 0
        return Decimal(total or 0), int(counted)


TRAINING_HOURS_DELIVERED = TrainingHoursDelivered(
    spec=MetricSpec(
        key="training_hours_delivered",
        title="Training Hours Delivered",
        definition="Sum of session durations. Hours delivered once, not once per attendee.",
        population=pop.DELIVERED_SESSIONS,
        provenance=Provenance.RESTATED,
        unit=Unit.HOURS,
        supports=PROGRAM_DIMENSIONS,
        note=(
            "Distinct from Learner Hours, which multiplies by attendance. A two-hour "
            "session with thirty people is 2 hours delivered and 60 learner hours; the "
            "workbook's single 'hours' column was read as both."
        ),
    )
)


@dataclass(frozen=True)
class TotalPrograms(ScalarMetric):
    def _measure(self, session: Session, filters: MetricFilters) -> tuple[Decimal | None, int]:
        rows = scope.programs(filters).subquery()
        total = session.scalar(select(func.count(distinct(rows.c.crm_program_id)))) or 0
        return Decimal(total), int(total)


TOTAL_PROGRAMS = TotalPrograms(
    spec=MetricSpec(
        key="total_programs",
        title="Total Programs",
        definition="Distinct programs the CRM marks completed.",
        population=pop.COMPLETED_PROGRAMS,
        provenance=Provenance.RESTATED,
        unit=Unit.COUNT,
        supports=PROGRAM_DIMENSIONS,
        note=(
            "Counted on computed_status, not status. The two disagree on 55 of 57 "
            "programs: off status the answer is 2, and it is 55. The workbook "
            "reported 25 because it knew only about programs somebody had pasted in."
        ),
    )
)


@dataclass(frozen=True)
class ProgramShare(RatioMetric):
    """A share of completed programs carrying some attribute value."""

    column: str = "type"
    wanted: str = "internal"

    def _measure(self, session: Session, filters: MetricFilters) -> tuple[Ratio, int, bool]:
        rows = scope.programs(filters).subquery()
        matching, total = session.execute(
            select(
                func.count().filter(rows.c[self.column] == self.wanted), func.count()
            ).select_from(rows)
        ).one()
        return (
            Ratio(Decimal(matching or 0), Decimal(total or 0)),
            int(total or 0),
            False,
        )


LND_DELIVERED_SHARE = ProgramShare(
    spec=MetricSpec(
        key="lnd_delivered_share",
        title="L&D-delivered %",
        definition="Share of completed programs the CRM marks as delivered by L&D.",
        population=pop.COMPLETED_PROGRAMS,
        provenance=Provenance.RENAMED,
        unit=Unit.PERCENT,
        supports=PROGRAM_DIMENSIONS,
        note=(
            "Was 'Internal %'. Same formula; the old name read as internal to the "
            "company, which is not what the flag distinguishes — `internal` means "
            "L&D delivered it, `external` means another department or a vendor did."
        ),
    ),
    column="type",
    wanted="internal",
)

PUBLIC_PROGRAM_SHARE = ProgramShare(
    spec=MetricSpec(
        key="public_program_share",
        title="Public vs Customised",
        definition=(
            "Share of completed programs open to the whole company rather than to one department."
        ),
        population=pop.COMPLETED_PROGRAMS,
        provenance=Provenance.RESTATED,
        unit=Unit.PERCENT,
        supports=PROGRAM_DIMENSIONS,
        note=(
            "Read from the CRM's target field, pending L&D confirming it matches their "
            "Public Calendar / Customised split. 50 public and 7 department today — an "
            "observation, not yet a definition."
        ),
    ),
    column="target",
    wanted="public",
)


# ---------------------------------------------------------------------------
# corrected — the published figure was wrong
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ParticipationRate(RatioMetric):
    """Distinct people who attended, over active employees who could have.

    The most consequential correction in the platform, and it was wrong twice
    over.

    P-01: the denominator was the literal number 192, typed into a formula. A
    hardcoded denominator does not move when the company does, so the rate drifts
    from reality silently and by an amount nobody can see.

    P-13: it was then five companies' attendance divided by one company's
    headcount. Numerator and denominator described different populations, which
    makes the quotient a number rather than a rate.

    Both halves now come from the same population, and the two are deliberately
    *not* filtered alike. The numerator is attendance within the period; the
    denominator is headcount at period end. Applying the period to the
    denominator too would count only employees who attended — a rate of exactly
    100%, arrived at honestly, and completely meaningless. `filters.without` is
    what makes that asymmetry explicit rather than accidental.
    """

    def _measure(self, session: Session, filters: MetricFilters) -> tuple[Ratio, int, bool]:
        # Everything except the period narrows both sides. A participation rate
        # for Finance is Finance's attendance over Finance's headcount.
        eligible_scope = filters.without(Dimension.PERIOD)
        eligible = scope.enrollable_employees(eligible_scope).subquery()
        headcount = session.scalar(select(func.count()).select_from(eligible)) or 0

        # The numerator counts only people who are *in* the denominator, and
        # this line is P-13. Without it: 288 people attended, but 87 of them
        # have since left and are not staff who could attend today — 19.9%
        # where the answer is 13.9%. A numerator drawn from one population over
        # a denominator drawn from another is a quotient, not a rate, and that
        # is exactly what the workbook published.
        attended = scope.attendances(filters).subquery()
        participants = (
            session.scalar(
                select(func.count(distinct(attended.c.employee_key))).where(
                    attended.c.employee_key.in_(select(eligible.c.employee_key))
                )
            )
            or 0
        )

        estimated = bool(
            session.scalar(
                select(func.count()).select_from(eligible).where(eligible.c.is_estimated)
            )
        )
        return Ratio(Decimal(participants), Decimal(headcount)), int(headcount), estimated


PARTICIPATION_RATE = ParticipationRate(
    spec=MetricSpec(
        key="participation_rate",
        title="Participation Rate",
        definition=(
            "Distinct people who attended at least one session in the period, over active "
            "employees the CRM can enroll as at the period end."
        ),
        population=pop.ENROLLABLE_EMPLOYEES,
        provenance=Provenance.CORRECTED,
        unit=Unit.PERCENT,
        supports=LEARNER_DIMENSIONS,
        note=(
            "Wrong twice in the workbook. The denominator was a hardcoded 192 (P-01), "
            "and it divided five companies' attendance by one company's headcount "
            "(P-13). Both halves now come from the same roster. Never present the old "
            "60.4% or 66.7%."
        ),
    )
)


# ---------------------------------------------------------------------------
# new — the workbook did not have these
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class NoShowRate(RatioMetric):
    """`(enrolled - attended) / enrolled`, per program.

    Depends entirely on enrollment being the enrolled list and not the roster.
    The CRM's `users[]` array is the union of enrolled people, walk-ins and
    survey-only respondents; counting it as enrollment inflates the denominator
    and pushes no-show towards 100%. `fact_enrollment` is built from
    `is_enrolled = true` alone, which is what makes this metric mean anything.
    """

    def _measure(self, session: Session, filters: MetricFilters) -> tuple[Ratio, int, bool]:
        enrolled_rows = scope.enrollments(filters).subquery()
        enrolled = session.scalar(select(func.count()).select_from(enrolled_rows)) or 0

        # A no-show is an enrollment with no matching attendance, matched on the
        # person *and* the program. Counting attendances and subtracting looks
        # equivalent and is not: a walk-in attends without enrolling, so every
        # one of them cancels out a genuine no-show and the rate reads 0% for a
        # program half the enrolled list skipped.
        attended_rows = scope.attendances(filters).subquery()
        no_shows = (
            session.scalar(
                select(func.count())
                .select_from(enrolled_rows)
                .where(
                    ~select(1)
                    .select_from(attended_rows)
                    .where(
                        attended_rows.c.employee_key == enrolled_rows.c.employee_key,
                        attended_rows.c.crm_program_id == enrolled_rows.c.crm_program_id,
                    )
                    .exists()
                )
            )
            or 0
        )
        return Ratio(Decimal(no_shows), Decimal(enrolled)), int(enrolled), False


NO_SHOW_RATE = NoShowRate(
    spec=MetricSpec(
        key="no_show_rate",
        title="No-show Rate",
        definition=(
            "Share of enrollments where the person never attended any session of the "
            "program they enrolled on."
        ),
        population=pop.ENROLLMENTS,
        provenance=Provenance.NEW,
        unit=Unit.PERCENT,
        supports=PROFILE_DIMENSIONS,
    )
)


@dataclass(frozen=True)
class FillRate(RatioMetric):
    """`enrollments / capacity`, over programs that declare a capacity.

    Programs with no capacity are excluded rather than treated as zero: a
    missing capacity is an unanswered question, and dividing by it would make
    the rate infinite or the program invisible depending on which way somebody
    coalesced it.
    """

    def _measure(self, session: Session, filters: MetricFilters) -> tuple[Ratio, int, bool]:
        programs = scope.programs(filters).subquery()
        capacity = (
            session.scalar(
                select(func.coalesce(func.sum(programs.c.capacity), 0)).where(
                    programs.c.capacity.is_not(None), programs.c.capacity > 0
                )
            )
            or 0
        )
        with_capacity = (
            session.scalar(
                select(func.count())
                .select_from(programs)
                .where(programs.c.capacity.is_not(None), programs.c.capacity > 0)
            )
            or 0
        )
        enrolled_rows = scope.enrollments(filters).subquery()
        enrolled = (
            session.scalar(
                select(func.count())
                .select_from(enrolled_rows)
                .where(
                    enrolled_rows.c.crm_program_id.in_(
                        select(programs.c.crm_program_id).where(
                            programs.c.capacity.is_not(None), programs.c.capacity > 0
                        )
                    )
                )
            )
            or 0
        )
        return Ratio(Decimal(enrolled), Decimal(capacity)), int(with_capacity), False


FILL_RATE = FillRate(
    spec=MetricSpec(
        key="fill_rate",
        title="Fill Rate",
        definition=("Enrollments over declared capacity, across programs that state a capacity."),
        population=pop.COMPLETED_PROGRAMS,
        provenance=Provenance.NEW,
        unit=Unit.PERCENT,
        supports=PROGRAM_DIMENSIONS,
        note="Programs with no declared capacity are excluded, not counted as zero.",
    )
)


@dataclass(frozen=True)
class SurveyResponseRate(RatioMetric):
    """Evaluations over distinct attendees.

    The denominator is people who attended, not people who enrolled: somebody
    who never turned up was never asked. It is the number that qualifies every
    quality score on the dashboard — a 98% logistics score over a 19% response
    rate is a different claim from the same score over 80%.
    """

    def _measure(self, session: Session, filters: MetricFilters) -> tuple[Ratio, int, bool]:
        # Both terms at the same grain: one person on one program. An evaluation
        # is already per person per program, so the denominator has to be too.
        #
        # Against distinct *people* this read 103.1% — 297 responses over 288
        # attendees — which is not a rate at all. Somebody who attended three
        # programs can return three surveys, and dividing responses by heads
        # compares a per-program count with a per-person one.
        attended_rows = scope.attendances(filters).subquery()
        attended_pairs = (
            select(attended_rows.c.employee_key, attended_rows.c.crm_program_id)
            .distinct()
            .subquery()
        )
        opportunities = session.scalar(select(func.count()).select_from(attended_pairs)) or 0

        evaluation_rows = scope.evaluations(filters).subquery()
        responses = session.scalar(select(func.count()).select_from(evaluation_rows)) or 0
        return Ratio(Decimal(responses), Decimal(opportunities)), int(opportunities), False


SURVEY_RESPONSE_RATE = SurveyResponseRate(
    spec=MetricSpec(
        key="survey_response_rate",
        title="Survey Response Rate",
        definition="Survey responses over distinct people who attended.",
        population=pop.ATTENDANCES,
        provenance=Provenance.NEW,
        unit=Unit.PERCENT,
        supports=PROFILE_DIMENSIONS,
        note=(
            "Qualifies every quality score. A high score over a low response rate "
            "is a weaker claim."
        ),
    )
)


@dataclass(frozen=True)
class CoverageGap(ScalarMetric):
    """Active employees with no attendance at all in the period.

    The complement of Participation Rate, reported as a count because it is
    actionable in a way a percentage is not: it is a list of names L&D can do
    something about, and it cannot be derived from attendance data alone — a
    person with zero attendance appears nowhere in the CRM's program payloads.
    """

    def _measure(self, session: Session, filters: MetricFilters) -> tuple[Decimal | None, int]:
        # The same statement the coverage view lists. Counting one expression
        # and listing another is how a list of 1,268 names comes to sit under a
        # heading that says 1,469.
        untrained = scope.count_of(session, scope.untrained_employees(filters))
        eligible = scope.enrollable_employees(filters.without(Dimension.PERIOD))
        return Decimal(untrained), scope.count_of(session, eligible)


COVERAGE_GAP = CoverageGap(
    spec=MetricSpec(
        key="coverage_gap",
        title="Coverage Gap",
        definition="Active employees with no attendance in the period.",
        population=pop.ENROLLABLE_EMPLOYEES,
        provenance=Provenance.NEW,
        unit=Unit.COUNT,
        supports=LEARNER_DIMENSIONS,
        note="Not derivable from attendance alone — these people appear in no program payload.",
    )
)


@dataclass(frozen=True)
class MonthsSinceLastTraining(ScalarMetric):
    """Median months since the last attendance, over people who have attended.

    Median rather than mean: the distribution has a long tail of people trained
    once eighteen months ago, and a mean would report a number no individual is
    near. People who have never attended are the Coverage Gap and are excluded
    here — including them would need an infinity or a zero, and both lie.
    """

    def _measure(self, session: Session, filters: MetricFilters) -> tuple[Decimal | None, int]:
        attended = scope.attendances(filters).subquery()
        latest = (
            select(
                attended.c.employee_key,
                func.max(attended.c.attended_date).label("last_attended"),
            )
            .group_by(attended.c.employee_key)
            .subquery()
        )
        # A literal date when one was asked for, the database's clock otherwise.
        # Bound as a Date so the subtraction stays date arithmetic — mixing a
        # Python date into it as text yields an interval Postgres will not take.
        as_of = (
            literal(filters.date_to, Date()) if filters.date_to is not None else func.current_date()
        )
        # 30.4375 = 365.25 / 12. Calendar months are unequal, and "4.0 months
        # since training" wants an even scale rather than a real one.
        # Subtracting two dates in PostgreSQL yields whole days as an integer,
        # not an interval — `extract(epoch from ...)` has nothing to take the
        # epoch of and the query fails outright.
        #
        # 30.4375 = 365.25 / 12. Calendar months are unequal, and "4.0 months
        # since training" wants an even scale rather than a real one.
        months = (as_of - latest.c.last_attended) / Decimal("30.4375")
        median = session.scalar(
            select(func.percentile_cont(0.5).within_group(months.asc())).select_from(latest)
        )
        people = session.scalar(select(func.count()).select_from(latest)) or 0
        return (None if median is None else Decimal(str(median))), int(people)


MONTHS_SINCE_LAST_TRAINING = MonthsSinceLastTraining(
    spec=MetricSpec(
        key="months_since_last_training",
        title="Months Since Last Training",
        definition=(
            "Median months since a person's most recent attendance, over people who have attended."
        ),
        population=pop.ATTENDANCES,
        provenance=Provenance.NEW,
        unit=Unit.MONTHS,
        supports=PROFILE_DIMENSIONS,
        note="Median, not mean — the tail is long and a mean would describe nobody.",
    )
)


# ---------------------------------------------------------------------------
# new, and awaiting a source
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PendingSource(ScalarMetric):
    """A declared metric whose source does not exist yet.

    Declared rather than omitted, and this is a deliberate choice. The catalogue
    is the contract: twenty-one metrics, each with a definition and a
    population. A metric left out of it is a metric nobody can see is missing,
    and "we never built LinkedIn reporting" becomes a discovery in month six
    rather than a line on the dashboard from day one.

    It returns no value and says why. It never returns zero — zero is a
    measurement, and there has been no measurement.
    """

    reason: str = "the source feed has not been provisioned"

    def compute(self, session: Session, filters: MetricFilters) -> MetricValue:
        self.spec.reject_unsupported(filters)
        return _value(self.spec, filters, value=None, sample_size=0)


LINKEDIN_DIMENSIONS = frozenset({Dimension.PERIOD, Dimension.SECTOR, Dimension.DEPARTMENT})

PENDING_NOTE = (
    "Awaiting the LinkedIn Learning export. The delivery plan puts it out of v1 for "
    "want of a feed, so this reports no value rather than a zero — there has been no "
    "measurement, which is not the same as a measurement of none."
)

LINKEDIN_HOURS = PendingSource(
    spec=MetricSpec(
        key="linkedin_hours",
        title="LinkedIn Hours",
        definition="Hours of LinkedIn Learning content completed, from the scheduled export.",
        population=pop.ATTENDANCES,
        provenance=Provenance.NEW,
        unit=Unit.HOURS,
        supports=LINKEDIN_DIMENSIONS,
        note=PENDING_NOTE,
    )
)

BLENDED_LEARNER_HOURS = PendingSource(
    spec=MetricSpec(
        key="blended_learner_hours",
        title="Blended Learner Hours",
        definition="Classroom learner hours plus LinkedIn Learning hours, for one blended total.",
        population=pop.ATTENDANCES,
        provenance=Provenance.NEW,
        unit=Unit.HOURS,
        supports=LINKEDIN_DIMENSIONS,
        note=PENDING_NOTE,
    )
)

UNIQUE_REACH = PendingSource(
    spec=MetricSpec(
        key="unique_reach",
        title="Unique Reach",
        definition=(
            "Distinct people reached by any learning, classroom or LinkedIn, counted once each."
        ),
        population=pop.ATTENDANCES,
        provenance=Provenance.NEW,
        unit=Unit.COUNT,
        supports=LINKEDIN_DIMENSIONS,
        note=(
            PENDING_NOTE
            + " Until then Total Participants is the classroom-only answer, and the two "
            "must not be presented as the same figure."
        ),
    )
)
