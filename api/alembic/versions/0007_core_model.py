"""The star schema, the enrichment overlay, and the exception queue.

Renumbered from 0006 on merge. This branch was cut before 0006_source_presence
— the nightly reconcile, FR-A08 — existed, and both claimed the same revision
id. Nothing here depends on that one, so it simply follows it.

Revision ID: 0007
Revises: 0006
Created: week 3

Thirteen tables across three schemas, and they are one revision rather than
three because they are one thing: `core` is meaningless without the overlay it
applies and the queue it writes what it could not resolve into. Splitting them
would leave two intermediate heads at which the transform cannot run, and a
migration you cannot stop at is a migration nobody should be able to stop at.

`raw` is untouched. It is append-only by grant, it already holds every payload,
and nothing here needs it to change — which is the point of having landed the
data before modelling it.

WHAT THE CONSTRAINTS ARE FOR

Each fact table's grain is a unique constraint over *source identifiers*, not
over surrogate keys. That is what makes the transform idempotent (FR-A10): the
30-minute pass re-reads every program and upserts, and a re-run cannot produce
a second attendance row for one scan. It is also, for `fact_attendance`, the
whole of the DUPLICATE_ATTENDANCE rule — one person cannot be recorded present
twice at one session because the database will not have it.

`dim_employee` carries two: a unique version window per person, and a partial
unique index asserting exactly one *current* row per person. Two current rows
would double that person in the headcount denominator, which is the defect
class the SCD exists to end (P-01). Enforced here rather than in the loader,
because the loader is the thing most likely to be wrong.

The two total CHECK constraints — `duration_derivable = (duration_hours IS NOT
NULL)` on `dim_session` and `(nps_band IS NULL) = (recommend_score IS NULL)` on
`fact_evaluation` — exist so that "excluded from the hour metrics" and "counted
as an NPS response" are each one boolean rather than two columns that can
disagree. A half-derived row is how a denominator and a numerator come to
describe different populations.

ROLLBACK

`downgrade()` drops all thirteen. That is safe for `core`, which is derived and
rebuildable from `raw` by definition, and destructive for `app` and
`ops.dq_exception`, which hold decisions people made and which no rebuild
reproduces. Anyone rolling this back past a populated overlay must dump those
five `app` tables first; nothing in the schema can make that judgement for
them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CORE = "core"
APP = "app"
OPS = "ops"

# Enum values are spelled out rather than imported from the models. A migration
# has to keep saying what it said on the day it ran: importing the enum would
# make this revision silently change meaning the next time somebody adds a
# member, and replaying history would then produce a database that never
# existed. The models and these lists are checked against each other by a test.
VALUE_SOURCE = ("crm", "override", "missing")
IDENTITY_STATUS = ("odoo_id", "employee_code", "email", "normalised_name", "unresolved")
NPS_BAND = ("promoter", "passive", "detractor")
PROGRAM_STATUS = ("upcoming", "running", "completed", "cancelled")
PROGRAM_TYPE = ("internal", "external")
PROGRAM_TARGET = ("public", "department")
EVALUATION_DIMENSION = (
    "knowledge_relevance",
    "activity_effectiveness",
    "logistics_effectiveness",
    "facilitator_performance",
    "recommend",
)
ENRICHMENT_FIELD = ("customised_department", "trainer_name")
DQ_RULE = (
    "identity_unresolved",
    "trainer_missing",
    "customised_dept_missing",
    "duration_underivable",
    "attendance_no_enrollment",
    "evaluation_no_attendance",
    "duplicate_attendance",
    "capacity_exceeded",
    "survey_question_unmapped",
    "survey_option_unscored",
    "attendee_outside_roster",
)
DQ_DISPOSITION = ("quarantined", "counted")
DQ_STATUS = ("open", "resolved", "dismissed")


def _enum(values: Sequence[str], name: str, *, length: int = 32) -> sa.Enum:
    """A VARCHAR with a CHECK, matching `lnd.models.columns.enum_column`.

    Not a native PostgreSQL enum: `ALTER TYPE ... ADD VALUE` has transaction
    restrictions that sit badly with migrations running as one transactional
    one-shot container, and revision 0005 showed that swapping a CHECK is four
    lines while swapping a type is not.
    """
    return sa.Enum(*values, name=name, native_enum=False, length=length, create_constraint=True)


# The author columns every `app` table carries (FR-C03, NFR-07). A changed
# value writes a new row and stamps `superseded_at` on the old one, so the
# prior values are retained rather than overwritten.
def _authored() -> list[sa.Column]:
    return [
        sa.Column("authored_by", sa.String(320), nullable=False),
        sa.Column(
            "authored_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
    ]


def upgrade() -> None:
    # ---------------------------------------------------------------- core
    op.create_table(
        "dim_date",
        sa.Column("date_key", sa.Date(), primary_key=True),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("quarter", sa.SmallInteger(), nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.Column("month_label", sa.String(24), nullable=False),
        sa.Column("month_start", sa.Date(), nullable=False),
        sa.Column("day_of_month", sa.SmallInteger(), nullable=False),
        sa.Column("day_of_week", sa.SmallInteger(), nullable=False),
        sa.Column("is_weekend", sa.Boolean(), nullable=False),
        sa.Column("fiscal_year", sa.SmallInteger(), nullable=False),
        sa.Column("fiscal_quarter", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="ck_dim_date_month_range"),
        sa.CheckConstraint("quarter BETWEEN 1 AND 4", name="ck_dim_date_quarter_range"),
        schema=CORE,
    )
    op.create_index("ix_dim_date_month_start", "dim_date", ["month_start"], schema=CORE)

    op.create_table(
        "dim_trainer",
        sa.Column("trainer_key", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("canonical_name", sa.String(200), nullable=False),
        sa.Column("normalised_name", sa.String(200), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("canonical_name", name="uq_dim_trainer_canonical_name"),
        schema=CORE,
    )

    op.create_table(
        "dim_employee",
        sa.Column("employee_key", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("odoo_id", sa.String(64), nullable=False),
        sa.Column("employee_code", sa.String(64), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("full_name", sa.String(200), nullable=True),
        sa.Column("normalised_name", sa.String(200), nullable=True),
        sa.Column("sector", sa.String(120), nullable=True),
        sa.Column("department_name", sa.String(200), nullable=True),
        sa.Column("company_name", sa.String(200), nullable=True),
        sa.Column("position_name", sa.String(200), nullable=True),
        sa.Column("job_level_name", sa.String(120), nullable=True),
        sa.Column("job_level_grade", sa.SmallInteger(), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column(
            "valid_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        # What was observed, kept apart from what was assumed. A first version
        # is backdated so the as-of query resolves over history the platform
        # was not running for; this is the moment the CRM actually told us.
        sa.Column(
            "observed_from",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("is_estimated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("attribute_hash", sa.String(80), nullable=False),
        sa.UniqueConstraint("odoo_id", "valid_from", name="uq_dim_employee_version"),
        sa.CheckConstraint(
            "is_current = (valid_to IS NULL)", name="ck_dim_employee_current_is_open"
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from", name="ck_dim_employee_window_ordered"
        ),
        # Total: estimated exactly when backdated. The flag cannot drift from
        # the thing it describes.
        sa.CheckConstraint(
            "is_estimated = (valid_from < observed_from)",
            name="ck_dim_employee_estimated_matches_backdating",
        ),
        schema=CORE,
    )
    # Exactly one current version per person. Two would double the headcount.
    op.create_index(
        "uq_dim_employee_current",
        "dim_employee",
        ["odoo_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
        schema=CORE,
    )
    op.create_index(
        "ix_dim_employee_asof",
        "dim_employee",
        ["odoo_id", "valid_from", "valid_to"],
        schema=CORE,
    )
    op.create_index("ix_dim_employee_employee_code", "dim_employee", ["employee_code"], schema=CORE)
    op.create_index("ix_dim_employee_email", "dim_employee", ["email"], schema=CORE)
    op.create_index(
        "ix_dim_employee_normalised_name", "dim_employee", ["normalised_name"], schema=CORE
    )
    op.create_index("ix_dim_employee_sector", "dim_employee", ["sector"], schema=CORE)

    op.create_table(
        "dim_program",
        sa.Column("crm_program_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("subtitle", sa.String(300), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        # Each enum column names its own CHECK. Two columns sharing a
        # constraint name is a duplicate-object error on the same table, and
        # `status` and `computed_status` are the same enum.
        sa.Column("status", _enum(PROGRAM_STATUS, "ck_dim_program_status"), nullable=True),
        sa.Column(
            "computed_status",
            _enum(PROGRAM_STATUS, "ck_dim_program_computed_status"),
            nullable=True,
        ),
        sa.Column("type", _enum(PROGRAM_TYPE, "ck_dim_program_type"), nullable=True),
        sa.Column("target", _enum(PROGRAM_TARGET, "ck_dim_program_target"), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("track_id", sa.Integer(), nullable=True),
        sa.Column("track_title", sa.String(300), nullable=True),
        sa.Column("parent_program_id", sa.Integer(), nullable=True),
        sa.Column("customised_department_name", sa.String(200), nullable=True),
        sa.Column(
            "customised_department_source",
            _enum(VALUE_SOURCE, "ck_dim_program_department_source"),
            nullable=False,
            server_default="missing",
        ),
        sa.Column("trainer_key", sa.BigInteger(), nullable=True),
        sa.Column(
            "trainer_source",
            _enum(VALUE_SOURCE, "ck_dim_program_trainer_source"),
            nullable=False,
            server_default="missing",
        ),
        sa.Column("deleted_at_source", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_record_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "transformed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["trainer_key"],
            [f"{CORE}.dim_trainer.trainer_key"],
            name="fk_dim_program_trainer",
        ),
        sa.CheckConstraint(
            "capacity IS NULL OR capacity >= 0", name="ck_dim_program_capacity_sane"
        ),
        schema=CORE,
    )
    op.create_index("ix_dim_program_status", "dim_program", ["computed_status"], schema=CORE)
    op.create_index("ix_dim_program_start_date", "dim_program", ["start_date"], schema=CORE)
    op.create_index("ix_dim_program_title", "dim_program", ["title"], schema=CORE)

    op.create_table(
        "dim_session",
        sa.Column("crm_session_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("crm_program_id", sa.Integer(), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("session_time_from", sa.Time(), nullable=True),
        sa.Column("session_time_to", sa.Time(), nullable=True),
        sa.Column("duration_hours", sa.Numeric(6, 2), nullable=True),
        sa.Column("duration_derivable", sa.Boolean(), nullable=False),
        sa.Column("trainer_key", sa.BigInteger(), nullable=True),
        sa.Column("trainer_name_raw", sa.String(200), nullable=True),
        sa.Column("location_name", sa.String(200), nullable=True),
        sa.Column("deleted_at_source", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_record_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "transformed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["crm_program_id"],
            [f"{CORE}.dim_program.crm_program_id"],
            name="fk_dim_session_program",
        ),
        sa.ForeignKeyConstraint(
            ["session_date"], [f"{CORE}.dim_date.date_key"], name="fk_dim_session_date"
        ),
        sa.ForeignKeyConstraint(
            ["trainer_key"], [f"{CORE}.dim_trainer.trainer_key"], name="fk_dim_session_trainer"
        ),
        sa.CheckConstraint(
            "duration_hours IS NULL OR duration_hours >= 0", name="ck_dim_session_duration_sane"
        ),
        sa.CheckConstraint(
            "duration_derivable = (duration_hours IS NOT NULL)",
            name="ck_dim_session_duration_flag_matches",
        ),
        schema=CORE,
    )
    op.create_index("ix_dim_session_program", "dim_session", ["crm_program_id"], schema=CORE)
    op.create_index("ix_dim_session_date", "dim_session", ["session_date"], schema=CORE)
    op.create_index("ix_dim_session_trainer", "dim_session", ["trainer_key"], schema=CORE)

    op.create_table(
        "fact_enrollment",
        sa.Column("enrollment_key", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("crm_program_id", sa.Integer(), nullable=False),
        sa.Column("employee_odoo_id", sa.String(64), nullable=False),
        sa.Column("employee_key", sa.BigInteger(), nullable=True),
        sa.Column(
            "identity_status",
            _enum(IDENTITY_STATUS, "ck_fact_enrollment_identity_status"),
            nullable=False,
        ),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enrolled_date", sa.Date(), nullable=True),
        sa.Column("deleted_at_source", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_record_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "transformed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["crm_program_id"],
            [f"{CORE}.dim_program.crm_program_id"],
            name="fk_fact_enrollment_program",
        ),
        sa.ForeignKeyConstraint(
            ["employee_key"],
            [f"{CORE}.dim_employee.employee_key"],
            name="fk_fact_enrollment_employee",
        ),
        sa.ForeignKeyConstraint(
            ["enrolled_date"], [f"{CORE}.dim_date.date_key"], name="fk_fact_enrollment_date"
        ),
        # The grain (FR-A10): one row per person per program, keyed on what the
        # source sent, so a re-run upserts and an unresolved person still gets
        # exactly one row.
        sa.UniqueConstraint("crm_program_id", "employee_odoo_id", name="uq_fact_enrollment_grain"),
        schema=CORE,
    )
    op.create_index("ix_fact_enrollment_employee", "fact_enrollment", ["employee_key"], schema=CORE)
    op.create_index("ix_fact_enrollment_date", "fact_enrollment", ["enrolled_date"], schema=CORE)

    op.create_table(
        "fact_attendance",
        sa.Column("attendance_key", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("crm_session_id", sa.Integer(), nullable=False),
        sa.Column("crm_program_id", sa.Integer(), nullable=False),
        sa.Column("crm_attendance_id", sa.BigInteger(), nullable=True),
        sa.Column("employee_odoo_id", sa.String(64), nullable=False),
        sa.Column("employee_key", sa.BigInteger(), nullable=True),
        sa.Column(
            "identity_status",
            _enum(IDENTITY_STATUS, "ck_fact_attendance_identity_status"),
            nullable=False,
        ),
        sa.Column("attended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attended_date", sa.Date(), nullable=False),
        sa.Column("learning_hours", sa.Numeric(6, 2), nullable=True),
        sa.Column("deleted_at_source", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_record_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "transformed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["crm_session_id"],
            [f"{CORE}.dim_session.crm_session_id"],
            name="fk_fact_attendance_session",
        ),
        sa.ForeignKeyConstraint(
            ["crm_program_id"],
            [f"{CORE}.dim_program.crm_program_id"],
            name="fk_fact_attendance_program",
        ),
        sa.ForeignKeyConstraint(
            ["employee_key"],
            [f"{CORE}.dim_employee.employee_key"],
            name="fk_fact_attendance_employee",
        ),
        sa.ForeignKeyConstraint(
            ["attended_date"], [f"{CORE}.dim_date.date_key"], name="fk_fact_attendance_date"
        ),
        # The grain, and the whole of the DUPLICATE_ATTENDANCE rule: one person
        # cannot be recorded present twice at one session. A double QR scan
        # deduplicates to this row and the duplicate is raised as an exception.
        sa.UniqueConstraint("crm_session_id", "employee_odoo_id", name="uq_fact_attendance_grain"),
        sa.CheckConstraint(
            "learning_hours IS NULL OR learning_hours >= 0", name="ck_fact_attendance_hours_sane"
        ),
        schema=CORE,
    )
    op.create_index(
        "ix_fact_attendance_program", "fact_attendance", ["crm_program_id"], schema=CORE
    )
    op.create_index("ix_fact_attendance_employee", "fact_attendance", ["employee_key"], schema=CORE)
    op.create_index("ix_fact_attendance_date", "fact_attendance", ["attended_date"], schema=CORE)
    op.create_index(
        "ix_fact_attendance_identity", "fact_attendance", ["identity_status"], schema=CORE
    )

    op.create_table(
        "fact_evaluation",
        sa.Column("evaluation_key", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("crm_program_id", sa.Integer(), nullable=False),
        sa.Column("crm_survey_id", sa.Integer(), nullable=True),
        sa.Column("employee_odoo_id", sa.String(64), nullable=False),
        sa.Column("employee_key", sa.BigInteger(), nullable=True),
        sa.Column(
            "identity_status",
            _enum(IDENTITY_STATUS, "ck_fact_evaluation_identity_status"),
            nullable=False,
        ),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responded_date", sa.Date(), nullable=True),
        sa.Column("score_knowledge_relevance", sa.SmallInteger(), nullable=True),
        sa.Column("score_activity_effectiveness", sa.SmallInteger(), nullable=True),
        sa.Column("score_logistics_effectiveness", sa.SmallInteger(), nullable=True),
        sa.Column("score_facilitator_performance", sa.SmallInteger(), nullable=True),
        sa.Column("recommend_score", sa.SmallInteger(), nullable=True),
        sa.Column("nps_band", _enum(NPS_BAND, "ck_fact_evaluation_nps_band"), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "unmapped_question_count",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("deleted_at_source", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_record_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "transformed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["crm_program_id"],
            [f"{CORE}.dim_program.crm_program_id"],
            name="fk_fact_evaluation_program",
        ),
        sa.ForeignKeyConstraint(
            ["employee_key"],
            [f"{CORE}.dim_employee.employee_key"],
            name="fk_fact_evaluation_employee",
        ),
        sa.ForeignKeyConstraint(
            ["responded_date"], [f"{CORE}.dim_date.date_key"], name="fk_fact_evaluation_date"
        ),
        # One response per person per program. A second submission supersedes
        # the first rather than adding a row; otherwise a keen respondent
        # weights their own program's NPS twice.
        sa.UniqueConstraint("crm_program_id", "employee_odoo_id", name="uq_fact_evaluation_grain"),
        sa.CheckConstraint(
            "(score_knowledge_relevance IS NULL OR score_knowledge_relevance BETWEEN 1 AND 5)"
            " AND (score_activity_effectiveness IS NULL"
            " OR score_activity_effectiveness BETWEEN 1 AND 5)"
            " AND (score_logistics_effectiveness IS NULL"
            " OR score_logistics_effectiveness BETWEEN 1 AND 5)"
            " AND (score_facilitator_performance IS NULL"
            " OR score_facilitator_performance BETWEEN 1 AND 5)",
            name="ck_fact_evaluation_scores_in_range",
        ),
        sa.CheckConstraint(
            "recommend_score IS NULL OR recommend_score BETWEEN 0 AND 10",
            name="ck_fact_evaluation_recommend_in_range",
        ),
        # A band exists exactly when a score does, so the numerator and the
        # denominator of NPS cannot disagree about how many responses there
        # were.
        sa.CheckConstraint(
            "(nps_band IS NULL) = (recommend_score IS NULL)",
            name="ck_fact_evaluation_band_matches_score",
        ),
        schema=CORE,
    )
    op.create_index("ix_fact_evaluation_employee", "fact_evaluation", ["employee_key"], schema=CORE)
    op.create_index("ix_fact_evaluation_date", "fact_evaluation", ["responded_date"], schema=CORE)
    op.create_index("ix_fact_evaluation_nps_band", "fact_evaluation", ["nps_band"], schema=CORE)

    # ----------------------------------------------------------------- app
    op.create_table(
        "enrichment_program_override",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        # Deliberately no foreign key to core.dim_program: enrichment must be
        # authorable for a program the transform has not reached, and dropping
        # core must never cascade into deleting L&D's decisions.
        sa.Column("crm_program_id", sa.Integer(), nullable=False),
        sa.Column("field", _enum(ENRICHMENT_FIELD, "enrichment_field"), nullable=False),
        sa.Column("value", sa.String(300), nullable=False),
        *_authored(),
        schema=APP,
    )
    op.create_index(
        "uq_program_override_live",
        "enrichment_program_override",
        ["crm_program_id", "field"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
        schema=APP,
    )
    op.create_index(
        "ix_program_override_program",
        "enrichment_program_override",
        ["crm_program_id"],
        schema=APP,
    )

    op.create_table(
        "trainer_alias",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("normalised_name", sa.String(200), nullable=False),
        sa.Column("trainer_key", sa.BigInteger(), nullable=False),
        *_authored(),
        sa.ForeignKeyConstraint(
            ["trainer_key"], [f"{CORE}.dim_trainer.trainer_key"], name="fk_trainer_alias_trainer"
        ),
        schema=APP,
    )
    op.create_index(
        "uq_trainer_alias_live",
        "trainer_alias",
        ["normalised_name"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
        schema=APP,
    )

    op.create_table(
        "survey_question_map",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("crm_survey_id", sa.Integer(), nullable=False),
        sa.Column("crm_question_id", sa.Integer(), nullable=False),
        sa.Column("question_title", sa.String(500), nullable=True),
        sa.Column("dimension", _enum(EVALUATION_DIMENSION, "evaluation_dimension"), nullable=False),
        sa.Column("scale_min", sa.SmallInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("scale_max", sa.SmallInteger(), nullable=False, server_default=sa.text("5")),
        *_authored(),
        sa.CheckConstraint("scale_max > scale_min", name="ck_survey_question_map_scale_ordered"),
        schema=APP,
    )
    op.create_index(
        "uq_survey_question_map_live",
        "survey_question_map",
        ["crm_survey_id", "crm_question_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
        schema=APP,
    )

    op.create_table(
        "survey_option_score",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("crm_question_id", sa.Integer(), nullable=False),
        sa.Column("crm_option_id", sa.Integer(), nullable=False),
        sa.Column("option_value", sa.String(300), nullable=True),
        sa.Column("score", sa.SmallInteger(), nullable=False),
        *_authored(),
        schema=APP,
    )
    op.create_index(
        "uq_survey_option_score_live",
        "survey_option_score",
        ["crm_question_id", "crm_option_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
        schema=APP,
    )

    op.create_table(
        "identity_mapping",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source_odoo_id", sa.String(64), nullable=False),
        sa.Column("target_odoo_id", sa.String(64), nullable=False),
        *_authored(),
        sa.CheckConstraint("source_odoo_id <> target_odoo_id", name="ck_identity_mapping_not_self"),
        schema=APP,
    )
    op.create_index(
        "uq_identity_mapping_live",
        "identity_mapping",
        ["source_odoo_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
        schema=APP,
    )

    # ----------------------------------------------------------------- ops
    op.create_table(
        "dq_exception",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("exception_key", sa.String(300), nullable=False),
        sa.Column("rule", _enum(DQ_RULE, "dq_rule"), nullable=False),
        sa.Column("disposition", _enum(DQ_DISPOSITION, "dq_disposition"), nullable=False),
        sa.Column("status", _enum(DQ_STATUS, "dq_status"), nullable=False, server_default="open"),
        sa.Column("crm_program_id", sa.Integer(), nullable=True),
        sa.Column("crm_session_id", sa.Integer(), nullable=True),
        sa.Column("employee_odoo_id", sa.String(64), nullable=True),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("details", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.String(320), nullable=True),
        sa.Column("closed_note", sa.Text(), nullable=True),
        # One row per violation, for the life of the violation. Not partial on
        # status: a dismissed exception must not be raisable again.
        sa.UniqueConstraint("exception_key", name="uq_dq_exception_key"),
        sa.CheckConstraint("occurrences >= 1", name="ck_dq_exception_occurrences_positive"),
        sa.CheckConstraint("last_seen_at >= first_seen_at", name="ck_dq_exception_seen_ordered"),
        sa.CheckConstraint(
            "(status = 'open') = (closed_at IS NULL)", name="ck_dq_exception_closed_is_terminal"
        ),
        schema=OPS,
    )
    op.create_index(
        "ix_dq_exception_open",
        "dq_exception",
        ["status", "rule", "first_seen_at"],
        schema=OPS,
    )
    op.create_index("ix_dq_exception_program", "dq_exception", ["crm_program_id"], schema=OPS)
    op.create_index("ix_dq_exception_last_seen", "dq_exception", ["last_seen_at"], schema=OPS)


def downgrade() -> None:
    # Children before parents. `core` is derived and rebuildable; the five
    # `app` tables and `ops.dq_exception` are not — see the module docstring.
    op.drop_table("dq_exception", schema=OPS)
    op.drop_table("identity_mapping", schema=APP)
    op.drop_table("survey_option_score", schema=APP)
    op.drop_table("survey_question_map", schema=APP)
    op.drop_table("trainer_alias", schema=APP)
    op.drop_table("enrichment_program_override", schema=APP)
    op.drop_table("fact_evaluation", schema=CORE)
    op.drop_table("fact_attendance", schema=CORE)
    op.drop_table("fact_enrollment", schema=CORE)
    op.drop_table("dim_session", schema=CORE)
    op.drop_table("dim_program", schema=CORE)
    op.drop_table("dim_employee", schema=CORE)
    op.drop_table("dim_trainer", schema=CORE)
    op.drop_table("dim_date", schema=CORE)
