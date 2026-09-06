"""The enrichment overlay: `app`.

The only human-authored data in the system. Everything in `core` is derived;
everything here is a decision somebody made, and the two are kept in separate
schemas so that "is this number from the CRM or from us?" is answerable by
looking at which schema it came from.

ENRICHMENT IS AN OVERLAY, NEVER AN EDIT

Synced data is never mutated. L&D's values live here, keyed to the source's own
identifiers, and are applied during transform (BRD §7.3). Two things follow:

  * If the CRM later starts supplying a field being maintained by hand, the
    transform prefers the CRM value and the override quietly retires with no
    migration and no data fix (FR-C04). `core.ValueSource` records which one
    won for each row.
  * Dropping and rebuilding `core` loses nothing. The overrides are not in it.

AND IT IS SUPERSEDED, NEVER UPDATED

Every table here keeps its history: a changed value writes a new row and stamps
`superseded_at` on the old one, and a partial unique index over the unsuperseded
rows is what guarantees exactly one live value per key. FR-C03 requires the
author, the timestamp and the prior values, and an UPDATE in place cannot
provide the third. The same shape as `raw` — for the same reason.

The module is `app_.py` because `app` is a package name in this codebase and
importing one over the other is a bug nobody enjoys finding.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from lnd.db import SCHEMA_APP, SCHEMA_CORE, Base
from lnd.models.columns import enum_column
from lnd.models.core import EvaluationDimension


class EnrichmentField(StrEnum):
    """Which program attribute an override supplies.

    One table with a field discriminator rather than a column per attribute.
    The set of hand-maintained fields shrinks over the project's life as the
    CRM supplies more of them, and retiring a field should be a stop-writing,
    not a migration.
    """

    CUSTOMISED_DEPARTMENT = "customised_department"
    TRAINER_NAME = "trainer_name"


class _Authored:
    """Author and validity columns, shared by every table in this schema.

    A mixin rather than four copies, because the audit obligation (FR-C03,
    NFR-07) is identical for all of them and a table that forgot one of these
    columns would be the one nobody could explain.
    """

    #: The signed-in user who made the decision. Email rather than an internal
    #: id: the IdP asserts it, it survives the person leaving, and it is what
    #: an auditor can actually resolve to a human.
    authored_by: Mapped[str] = mapped_column(String(320), nullable=False)
    authored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: Null on the live row. Set when a newer row replaces this one, or when a
    #: person retires the value deliberately.
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Why, in the author's words. Optional for an override, and the whole
    #: point of a dismissal (FR-F03).
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProgramOverride(Base, _Authored):
    """One hand-supplied value for one program attribute (FR-C01 to FR-C05).

    Never written back to the CRM. There is no CRM write client in this
    codebase and that is the enforcement (FR-C05).
    """

    __tablename__ = "enrichment_program_override"
    __table_args__ = (
        # Exactly one live value per (program, field). The partial predicate is
        # what lets the same pair recur through history once superseded — the
        # same mechanism as `ops.alert_notification`'s live index.
        Index(
            "uq_program_override_live",
            "crm_program_id",
            "field",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
        Index("ix_program_override_program", "crm_program_id"),
        {"schema": SCHEMA_APP},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    #: Deliberately not a foreign key to `core.dim_program`. Enrichment must be
    #: authorable for a program the transform has not reached yet, and dropping
    #: and rebuilding `core` must never cascade into deleting L&D's decisions.
    crm_program_id: Mapped[int] = mapped_column(Integer, nullable=False)
    field: Mapped[EnrichmentField] = mapped_column(
        enum_column(EnrichmentField, "enrichment_field"), nullable=False
    )
    #: Text for every field. A department name and a trainer name are both
    #: names, and typing this per field would mean a column per field, which
    #: is the design this table exists to avoid.
    value: Mapped[str] = mapped_column(String(300), nullable=False)

    def __repr__(self) -> str:
        state = "live" if self.superseded_at is None else "superseded"
        return f"<ProgramOverride {self.crm_program_id} {self.field}={self.value!r} {state}>"


class TrainerAlias(Base, _Authored):
    """One observed trainer spelling, merged into one canonical trainer (P-04).

    The CRM stores a trainer as free text on the session, so `Ahmed Nasr`,
    `ahmed nasr` and `A. Nasr` arrive as three trainers. The transform gives
    each distinct normalised spelling its own `dim_trainer` row — it must,
    since guessing that two names are one person is not a machine's decision —
    and a row here is a person saying "these two are the same". FR-B06.

    Only human merges live here. The auto-created trainers are in `core`,
    because they are derived and a rebuild reproduces them exactly; a merge is
    a decision and a rebuild must not lose it.
    """

    __tablename__ = "trainer_alias"
    __table_args__ = (
        Index(
            "uq_trainer_alias_live",
            "normalised_name",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
        {"schema": SCHEMA_APP},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    #: The spelling as the conformer normalises it — case-folded, whitespace
    #: collapsed, accents stripped. Matching on the raw string would need one
    #: alias row per trailing space.
    normalised_name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: The trainer this spelling really means. A foreign key, unlike the
    #: program override above: the target is a `core` row that the transform
    #: itself created, so it cannot be authored ahead of the transform, and an
    #: alias pointing at a trainer that no longer exists is a broken merge
    #: rather than a pending one.
    trainer_key: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA_CORE}.dim_trainer.trainer_key"), nullable=False
    )

    def __repr__(self) -> str:
        return f"<TrainerAlias {self.normalised_name!r} -> {self.trainer_key}>"


class SurveyQuestionMap(Base, _Authored):
    """Which of the five measured dimensions a survey question is asking about.

    THIS TABLE EXISTS BECAUSE THE BRD'S ASSUMPTION WAS WRONG

    BRD §8.2 specifies `q1` to `q5` on `fact_evaluation`, and §9 defines four
    quality metrics and NPS as counts over them. That presumes one fixed survey
    across all programs. The live API gives each program its own `survey`, with
    its own `question_id`s and its own option values, and nothing in the
    payload says which question is the facilitator question.

    So the association is a decision, and decisions live in `app`. Without a
    row here a scored answer cannot be attributed to a metric, and the
    transform raises SURVEY_QUESTION_UNMAPPED rather than guessing — a guess
    would silently move four published percentages and NPS.

    `scale_max` is carried because the recommend question is conventionally
    0-10 while the quality questions are 1-5, and the band boundaries are
    meaningless without knowing which.
    """

    __tablename__ = "survey_question_map"
    __table_args__ = (
        Index(
            "uq_survey_question_map_live",
            "crm_survey_id",
            "crm_question_id",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
        # A question id is only unique within its survey — the live fixtures
        # show a survey question and an assessment question both numbered 42 —
        # so the survey is part of the key, not context.
        CheckConstraint("scale_max > scale_min", name="ck_survey_question_map_scale_ordered"),
        {"schema": SCHEMA_APP},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    crm_survey_id: Mapped[int] = mapped_column(Integer, nullable=False)
    crm_question_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Kept as a denormalised copy so the mapping screen can show what was
    #: mapped without joining back through `raw`, and so a question whose
    #: wording changed at source is visible as a mismatch.
    question_title: Mapped[str | None] = mapped_column(String(500), nullable=True)

    dimension: Mapped[EvaluationDimension] = mapped_column(
        enum_column(EvaluationDimension, "evaluation_dimension"), nullable=False
    )
    scale_min: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"))
    scale_max: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("5"))

    def __repr__(self) -> str:
        return (
            f"<SurveyQuestionMap survey={self.crm_survey_id} "
            f"q={self.crm_question_id} -> {self.dimension}>"
        )


class SurveyOptionScore(Base, _Authored):
    """The numeric value of one selectable answer.

    A `select` answer arrives as the option's text — "Very useful" — and a
    percentage of respondents scoring four or better cannot be computed from a
    string. Which words mean four is a judgement about a particular survey's
    wording, so it is authored rather than inferred: a lexicon mapping
    "Very useful" to 5 would be right until the day a survey offers
    "Somewhat useful" as its top option.

    Not needed for `rating` answers, which arrive numeric.
    """

    __tablename__ = "survey_option_score"
    __table_args__ = (
        Index(
            "uq_survey_option_score_live",
            "crm_question_id",
            "crm_option_id",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
        {"schema": SCHEMA_APP},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    crm_question_id: Mapped[int] = mapped_column(Integer, nullable=False)
    crm_option_id: Mapped[int] = mapped_column(Integer, nullable=False)
    option_value: Mapped[str | None] = mapped_column(String(300), nullable=True)
    score: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    def __repr__(self) -> str:
        return f"<SurveyOptionScore q={self.crm_question_id} {self.option_value!r}={self.score}>"


class IdentityMapping(Base, _Authored):
    """A person, mapped by hand to an employee the automatic rules missed.

    The BRD calls this `identity_resolution`. FR-B04's three probes — employee
    code, then email, then normalised name — resolve most attendees; a
    contractor, a new hire with no code yet, or someone whose CRM user record
    was never created resolves to nobody. FR-B05 says quarantine, never drop,
    and FR-F03 says an operator may resolve the exception by mapping to an
    existing entity. This is that mapping.

    It is deliberately a *fourth probe* rather than a correction applied after
    the fact: the transform consults it in the same pass as the other three, so
    a mapped attendee's history is rebuilt correctly on the next run rather
    than needing a patch.
    """

    __tablename__ = "identity_mapping"
    __table_args__ = (
        Index(
            "uq_identity_mapping_live",
            "source_odoo_id",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
        CheckConstraint("source_odoo_id <> target_odoo_id", name="ck_identity_mapping_not_self"),
        {"schema": SCHEMA_APP},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    #: The unresolvable identifier as it appears on the attendance row.
    source_odoo_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The `odoo_id` of the person it really is. Points at the natural key
    #: rather than at `dim_employee.employee_key`, because that surrogate names
    #: one *version* of the person and a mapping is about the person.
    target_odoo_id: Mapped[str] = mapped_column(String(64), nullable=False)

    def __repr__(self) -> str:
        return f"<IdentityMapping {self.source_odoo_id} -> {self.target_odoo_id}>"
