"""Programme and trainer scorecards.

Every figure here is `registry.compute` with the filters narrowed to one
programme or one trainer. None of the arithmetic is repeated: No-show Rate,
Fill Rate and Survey Response Rate are already in the registry with their
populations declared and their values pinned by the CI gate, and a second
implementation of any of them would be free to disagree with the first.

TWO METRICS CANNOT BE NARROWED BY TRAINER, AND THAT IS NOT AN OVERSIGHT

An attendance joins to its session, and a session names its trainer — so Total
Participants and Learner Hours honour a trainer filter directly. A survey
response does not: its grain is one person on one *programme*, and the payload
never says which of that programme's sessions the respondent sat in. Narrowing
NPS by trainer would filter nothing at all while appearing to, or — worse, if
somebody joined it through attendance — filter the denominator and not the
numerator, which is the shape of P-13.

So a trainer's quality figures are computed over *the programmes that trainer
delivered*, and the scorecard says so in those words. That is a weaker claim
than "this trainer's NPS", and it is the true one: on a programme two trainers
shared, both carry the same responses. The alternative was to attribute
feedback to a person the data does not attribute it to.

THE WEIGHTING IS THE REGISTRY'S, NOT OURS

Asking NPS for a set of programme ids returns one numerator over one
denominator across the set, which *is* the weighted answer — 100% over 2
responses and 80% over 98 combine to 80.4%, never to 90%. The per-programme
rows beside it are the same metric under narrower filters, and they sum to the
whole by construction rather than by arithmetic written here. `test_scorecards`
asserts that sum, so a change that broke it would fail rather than round.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lnd.metrics import registry, scope
from lnd.metrics.base import MetricValue
from lnd.metrics.filters import Dimension, EmptyScope, MetricFilters, UnsupportedFilter
from lnd.models.core import (
    DimProgram,
    DimSession,
    DimTrainer,
    FactEvaluation,
)

#: A programme's own figures, in reading order: how much was delivered, how
#: full it was, who turned up, and what they thought of it.
PROGRAM_FIGURES = (
    "training_days",
    "training_hours_delivered",
    "total_participants",
    "learner_hours",
    "fill_rate",
    "no_show_rate",
    "survey_response_rate",
    "nps",
    "knowledge_relevance",
    "activity_effectiveness",
    "logistics_effectiveness",
    "facilitator_performance",
)

#: What a trainer filter can honestly narrow: programmes, sessions and the
#: attendance that hangs off them.
TRAINER_DELIVERY_FIGURES = (
    "total_programs",
    "training_days",
    "training_hours_delivered",
    "total_participants",
    "learner_hours",
    "fill_rate",
)

#: What is answerable only at the grain of the programmes they delivered.
TRAINER_PROGRAM_FIGURES = (
    "nps",
    "knowledge_relevance",
    "activity_effectiveness",
    "logistics_effectiveness",
    "facilitator_performance",
    "survey_response_rate",
    "no_show_rate",
)


@dataclass(frozen=True)
class ProgramHeader:
    crm_program_id: int
    title: str
    status: str | None
    type: str | None
    target: str | None
    capacity: int | None
    start_date: date | None
    end_date: date | None
    customised_department_name: str | None
    trainer_names: tuple[str, ...]


@dataclass(frozen=True)
class TrainerHeader:
    trainer_key: int
    canonical_name: str
    #: Not a person. Ranked alongside people, `L&D Team` reads as one.
    is_placeholder: bool
    #: An outside vendor. Their score is a fact about a supplier.
    is_external: bool


@dataclass(frozen=True)
class Comment:
    """One free-text answer, with the scores that came with it.

    Deliberately without the respondent's name. Every other row on this
    platform is attributable and this one is not: a comment that arrives beside
    the writer's name is a comment people write differently, and the scorecard's
    purpose is to explain a quality score rather than to identify who said what.
    The name is still in `fact_evaluation` for anyone who has cause to look.
    """

    program_title: str
    responded_date: date | None
    recommend_score: int | None
    nps_band: str | None
    text: str


@dataclass(frozen=True)
class SessionRow:
    crm_session_id: int
    session_date: date
    duration_hours: Decimal | None
    duration_derivable: bool
    trainer: str | None
    location: str | None
    attendees: int


@dataclass(frozen=True)
class ProgramScorecard:
    program: ProgramHeader
    figures: tuple[MetricValue, ...]
    sessions: tuple[SessionRow, ...]
    comments: tuple[Comment, ...]
    filters_applied: str


@dataclass(frozen=True)
class ProgramContribution:
    """One programme's contribution to a trainer's combined figure."""

    crm_program_id: int
    title: str
    value: MetricValue


@dataclass(frozen=True)
class TrainerScorecard:
    trainer: TrainerHeader
    #: Narrowed by trainer: sessions, hours, programmes, people reached.
    delivery: tuple[MetricValue, ...]
    #: Narrowed to the programmes they delivered — a weaker claim, stated as one.
    programme_level: tuple[MetricValue, ...]
    #: The programmes behind `programme_level`, each with its own NPS. They sum
    #: to the combined figure; they do not average to it.
    nps_by_program: tuple[ProgramContribution, ...]
    program_ids: tuple[int, ...]
    filters_applied: str


class UnknownProgram(KeyError):
    """No such programme, or none the current filters leave in scope."""


class UnknownTrainer(KeyError):
    """No such trainer."""


def _figures(
    session: Session, keys: tuple[str, ...], filters: MetricFilters
) -> tuple[MetricValue, ...]:
    """Compute each named metric, skipping those the filters put out of reach.

    A metric that refuses the filters is absent rather than an error, exactly as
    on the dashboard: asked for one trainer, Participation Rate has no
    population, and its absence is the honest answer to a question that has
    none.
    """
    values: list[MetricValue] = []
    for key in keys:
        try:
            values.append(registry.compute(key, session, filters))
        except UnsupportedFilter:
            continue
    return tuple(values)


def _program_header(session: Session, program_id: int) -> ProgramHeader:
    program = session.get(DimProgram, program_id)
    if program is None or program.deleted_at_source is not None:
        raise UnknownProgram(f"no programme {program_id}")

    trainers = session.scalars(
        select(DimTrainer.canonical_name)
        .join(DimSession, DimSession.trainer_key == DimTrainer.trainer_key)
        .where(DimSession.crm_program_id == program_id, DimSession.deleted_at_source.is_(None))
        .distinct()
        .order_by(DimTrainer.canonical_name)
    ).all()

    return ProgramHeader(
        crm_program_id=program.crm_program_id,
        title=program.title,
        status=program.computed_status.value if program.computed_status else None,
        type=program.type.value if program.type else None,
        target=program.target.value if program.target else None,
        capacity=program.capacity,
        start_date=program.start_date,
        end_date=program.end_date,
        customised_department_name=program.customised_department_name,
        trainer_names=tuple(str(name) for name in trainers),
    )


def _sessions(session: Session, filters: MetricFilters) -> tuple[SessionRow, ...]:
    """The programme's sessions, with a head count each.

    Counted from `scope.attendances` under the same filters the figures use, so
    the column adds up to the participants figure above it rather than to a
    separate idea of who attended.
    """
    attended = scope.attendances(filters).subquery()
    counts = (
        select(attended.c.crm_session_id, func.count().label("attendees"))
        .group_by(attended.c.crm_session_id)
        .subquery()
    )
    scoped = scope.sessions(filters).subquery()
    rows = session.execute(
        select(
            DimSession.crm_session_id,
            DimSession.session_date,
            DimSession.duration_hours,
            DimSession.duration_derivable,
            DimTrainer.canonical_name.label("trainer"),
            DimSession.location_name.label("location"),
            func.coalesce(counts.c.attendees, 0).label("attendees"),
        )
        .join(DimTrainer, DimSession.trainer_key == DimTrainer.trainer_key, isouter=True)
        .join(counts, counts.c.crm_session_id == DimSession.crm_session_id, isouter=True)
        .where(DimSession.crm_session_id.in_(select(scoped.c.crm_session_id)))
        .order_by(DimSession.session_date)
    ).mappings()
    return tuple(SessionRow(**row) for row in rows)


def _comments(session: Session, filters: MetricFilters) -> tuple[Comment, ...]:
    """Every free-text answer in scope, whole.

    Not truncated to a preview, and this is the one place on the platform where
    that matters: 88 comments across 32 programmes are the only qualitative
    signal there is, and the only place a 91.9% logistics score has an
    explanation. A preview turns the explanation into decoration.
    """
    scoped = scope.evaluations(filters).subquery()
    rows = session.execute(
        select(
            DimProgram.title.label("program_title"),
            FactEvaluation.responded_date,
            FactEvaluation.recommend_score,
            FactEvaluation.nps_band,
            FactEvaluation.comment,
        )
        .join(DimProgram, FactEvaluation.crm_program_id == DimProgram.crm_program_id)
        .where(
            FactEvaluation.evaluation_key.in_(select(scoped.c.evaluation_key)),
            FactEvaluation.comment.is_not(None),
            func.length(func.trim(FactEvaluation.comment)) > 0,
        )
        .order_by(FactEvaluation.responded_date.desc().nulls_last())
    ).mappings()
    return tuple(
        Comment(
            program_title=row["program_title"],
            responded_date=row["responded_date"],
            recommend_score=row["recommend_score"],
            nps_band=row["nps_band"].value if row["nps_band"] is not None else None,
            text=str(row["comment"]).strip(),
        )
        for row in rows
    )


def program_scorecard(
    session: Session, program_id: int, filters: MetricFilters | None = None
) -> ProgramScorecard:
    """One programme: what was delivered, how full, who came, what they said."""
    applied = (filters or MetricFilters()).narrowed_to(Dimension.PROGRAM, str(program_id))
    header = _program_header(session, program_id)

    return ProgramScorecard(
        program=header,
        figures=_figures(session, PROGRAM_FIGURES, applied),
        sessions=_sessions(session, applied),
        comments=_comments(session, applied),
        filters_applied=applied.describe(),
    )


def _trainer_header(session: Session, trainer_key: int) -> TrainerHeader:
    trainer = session.get(DimTrainer, trainer_key)
    if trainer is None:
        raise UnknownTrainer(f"no trainer {trainer_key}")
    return TrainerHeader(
        trainer_key=trainer.trainer_key,
        canonical_name=trainer.canonical_name,
        is_placeholder=trainer.is_placeholder,
        is_external=trainer.is_external,
    )


def programs_delivered(session: Session, filters: MetricFilters) -> tuple[int, ...]:
    """The programmes this trainer's sessions belong to, within the filters.

    Read from the sessions rather than from `dim_program.trainer_key`: the
    programme-level trainer is set only where every session agrees, so a
    programme two people shared would otherwise belong to neither of them.
    """
    scoped = scope.sessions(filters).subquery()
    return tuple(
        session.scalars(
            select(scoped.c.crm_program_id).distinct().order_by(scoped.c.crm_program_id)
        ).all()
    )


def trainer_scorecard(
    session: Session, trainer_key: int, filters: MetricFilters | None = None
) -> TrainerScorecard:
    """One trainer: what they delivered, and how their programmes were received."""
    base = filters or MetricFilters()
    header = _trainer_header(session, trainer_key)
    pinned = base.narrowed_to(Dimension.TRAINER, str(trainer_key))

    program_ids = programs_delivered(session, pinned)
    if not program_ids:
        # Nothing in scope. Reported as an empty scorecard rather than as the
        # platform's figures under this person's name.
        return TrainerScorecard(
            trainer=header,
            delivery=_figures(session, TRAINER_DELIVERY_FIGURES, pinned),
            programme_level=(),
            nps_by_program=(),
            program_ids=(),
            filters_applied=pinned.describe(),
        )

    try:
        across = base.narrowed_within(Dimension.PROGRAM, frozenset(program_ids))
    except EmptyScope:
        across = None

    programme_level: tuple[MetricValue, ...] = ()
    contributions: tuple[ProgramContribution, ...] = ()
    if across is not None:
        programme_level = _figures(session, TRAINER_PROGRAM_FIGURES, across)
        titles: dict[int, str] = dict(
            session.execute(
                select(DimProgram.crm_program_id, DimProgram.title).where(
                    DimProgram.crm_program_id.in_(program_ids)
                )
            ).all()  # type: ignore[arg-type]
        )
        contributions = tuple(
            ProgramContribution(
                crm_program_id=program_id,
                title=titles.get(program_id, str(program_id)),
                # The same metric, one programme at a time. Their numerators and
                # denominators sum to the combined figure; their percentages do
                # not average to it.
                value=registry.compute(
                    "nps", session, across.narrowed_to(Dimension.PROGRAM, str(program_id))
                ),
            )
            for program_id in program_ids
        )

    return TrainerScorecard(
        trainer=header,
        delivery=_figures(session, TRAINER_DELIVERY_FIGURES, pinned),
        programme_level=programme_level,
        nps_by_program=contributions,
        program_ids=program_ids,
        filters_applied=pinned.describe(),
    )


def program_index(session: Session, filters: MetricFilters | None = None) -> list[dict[str, Any]]:
    """Every programme in scope, with enough to choose one — or chart one.

    `participants` and `enrollments` are here because the workbook's two widest
    charts are per-programme: *Participation per Program* and *Enrollment Vs
    Participation*. Drawing those from fifty-five scorecard requests would be
    fifty-five round trips for one picture.

    They are counted from `scope.attendances` and `scope.enrollments` — the same
    statements every metric is built on — so a bar on the chart and the figure
    on that programme's scorecard cannot disagree. Counting them here with a
    query written for the chart is how a picture comes to tell a different story
    from the number beside it.
    """
    applied = filters or MetricFilters()
    scoped = scope.programs(applied).subquery()

    rows = session.execute(
        select(
            DimProgram.crm_program_id,
            DimProgram.title,
            DimProgram.type,
            DimProgram.target,
            DimProgram.capacity,
            DimProgram.start_date,
            DimProgram.end_date,
        )
        .where(DimProgram.crm_program_id.in_(select(scoped.c.crm_program_id)))
        .order_by(DimProgram.start_date.desc().nulls_last(), DimProgram.title)
    ).mappings()

    index = [dict(row) for row in rows]
    counts = _per_program_counts(session, applied)
    for row in index:
        pair = counts.get(int(row["crm_program_id"]), (0, 0))
        row["participants"], row["enrollments"] = pair
    return index


def _per_program_counts(session: Session, filters: MetricFilters) -> dict[int, tuple[int, int]]:
    """Distinct attendees and enrollments per programme, one pass each.

    Both fact tables carry `crm_program_id`, so neither needs a join — and both
    are read through the scope statements every metric is built on, so a bar and
    the figure on that programme's scorecard cannot disagree.

    Participants are *distinct people*; enrollments are rows. That asymmetry is
    the point of the chart the pair feeds: somebody enrolled on a programme and
    absent from it is the gap the two bars show.
    """
    attendance = scope.attendances(filters).subquery()
    people: dict[int, int] = {
        int(program_id): int(count)
        for program_id, count in session.execute(
            select(
                attendance.c.crm_program_id,
                func.count(func.distinct(attendance.c.employee_key)),
            ).group_by(attendance.c.crm_program_id)
        ).all()
    }

    enrollment = scope.enrollments(filters).subquery()
    enrolled: dict[int, int] = {
        int(program_id): int(count)
        for program_id, count in session.execute(
            select(enrollment.c.crm_program_id, func.count()).group_by(enrollment.c.crm_program_id)
        ).all()
    }

    return {
        program_id: (people.get(program_id, 0), enrolled.get(program_id, 0))
        for program_id in set(people) | set(enrolled)
    }


def trainer_index(session: Session, filters: MetricFilters | None = None) -> list[dict[str, Any]]:
    """Every trainer, with the three counts that make them comparable.

    Sessions, programmes and attendances together, because they answer
    different questions and a list showing one invites the wrong comparison:
    the trainer with the most attendances here has one of the smallest
    catalogues.
    """
    applied = filters or MetricFilters()
    sessions = scope.sessions(applied).subquery()
    attended = scope.attendances(applied).subquery()

    delivered = (
        select(
            sessions.c.trainer_key,
            func.count().label("sessions"),
            func.count(func.distinct(sessions.c.crm_program_id)).label("programs"),
        )
        .where(sessions.c.trainer_key.is_not(None))
        .group_by(sessions.c.trainer_key)
        .subquery()
    )
    # Attendance carries no trainer, so it is counted through the session that
    # does — the same join `scope.attendances` uses for a trainer filter.
    reached = (
        select(DimSession.trainer_key, func.count().label("attendances"))
        .join(attended, attended.c.crm_session_id == DimSession.crm_session_id)
        .where(DimSession.trainer_key.is_not(None))
        .group_by(DimSession.trainer_key)
        .subquery()
    )

    rows = session.execute(
        select(
            DimTrainer.trainer_key,
            DimTrainer.canonical_name,
            DimTrainer.is_placeholder,
            DimTrainer.is_external,
            func.coalesce(delivered.c.sessions, 0).label("sessions"),
            func.coalesce(delivered.c.programs, 0).label("programs"),
            func.coalesce(reached.c.attendances, 0).label("attendances"),
        )
        .join(delivered, delivered.c.trainer_key == DimTrainer.trainer_key, isouter=True)
        .join(reached, reached.c.trainer_key == DimTrainer.trainer_key, isouter=True)
        .order_by(func.coalesce(delivered.c.sessions, 0).desc(), DimTrainer.canonical_name)
    ).mappings()
    return [dict(row) for row in rows]


__all__ = [
    "PROGRAM_FIGURES",
    "TRAINER_DELIVERY_FIGURES",
    "TRAINER_PROGRAM_FIGURES",
    "ProgramScorecard",
    "TrainerScorecard",
    "UnknownProgram",
    "UnknownTrainer",
    "program_index",
    "program_scorecard",
    "programs_delivered",
    "trainer_index",
    "trainer_scorecard",
]
