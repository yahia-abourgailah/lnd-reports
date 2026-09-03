"""ops.sync_run — the audit of every sync attempt, and the watermark store.

Revision ID: 0002
Revises: 0001
Created: week 2

One row per attempt to pull one entity from one source. It is both the audit
trail (FR-A09) and the place the incremental watermark is read back from
(FR-A07), on purpose: a separate cursor table can claim a position no successful
run supports, and then nobody can say whether the gap was real.

The enum values below are written out literally rather than imported from
`lnd.models.ops`. A migration is a frozen statement of what the schema was at
this revision; importing the application's enums would silently rewrite this
file's meaning the day a fifth source is added, and replaying history from empty
would stop reproducing the database that history actually produced.

No grants are issued here. `0001` set ALTER DEFAULT PRIVILEGES on `ops` for
`lnd_app`, so this table and its sequence are covered the moment they exist.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> sa.Enum:
    """VARCHAR(32) plus a CHECK, not a native PostgreSQL enum type.

    `ALTER TYPE ... ADD VALUE` carries transaction restrictions that sit badly
    with migrations run as a single transactional one-shot container. A CHECK
    gives the same guarantee and extending it is an ordinary constraint swap.
    """
    return sa.Enum(
        *values,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
    )


def upgrade() -> None:
    op.create_table(
        "sync_run",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        # -- what ran -------------------------------------------------------
        sa.Column(
            "source", _enum("sync_source", "crm", "forms", "hris", "linkedin"), nullable=False
        ),
        sa.Column(
            "entity",
            _enum(
                "sync_entity",
                "program",
                "session",
                "enrollment",
                "attendance",
                "feedback",
                "employee",
                "course_activity",
            ),
            nullable=False,
        ),
        sa.Column(
            "mode", _enum("sync_mode", "incremental", "full_reconcile", "backfill"), nullable=False
        ),
        sa.Column(
            "triggered_by",
            _enum("sync_trigger", "scheduled", "manual"),
            server_default="scheduled",
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum("sync_status", "running", "success", "failed", "skipped"),
            nullable=False,
        ),
        # -- when -----------------------------------------------------------
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        # -- the window this run covered -------------------------------------
        sa.Column("watermark_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("watermark_to", sa.DateTime(timezone=True), nullable=True),
        # -- what it moved ---------------------------------------------------
        sa.Column("records_fetched", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("records_written", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("records_deleted", sa.Integer(), server_default=sa.text("0"), nullable=False),
        # -- how it went ------------------------------------------------------
        sa.Column("attempts", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("error_type", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        # -- the thread back to the logs --------------------------------------
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_sync_run"),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_sync_run_finished_after_started",
        ),
        # Exactly the running rows are the unfinished ones. Both operands are
        # NOT NULL-safe, so the check is total rather than vacuously true on a
        # null.
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name="ck_sync_run_terminal_is_finished",
        ),
        sa.CheckConstraint(
            "records_fetched >= 0 AND records_written >= 0 AND records_deleted >= 0",
            name="ck_sync_run_counts_non_negative",
        ),
        sa.CheckConstraint("attempts >= 1", name="ck_sync_run_attempts_positive"),
        schema="ops",
    )

    # At most one run in flight per (source, entity). Beat fires every 30
    # minutes and `task_acks_late` redelivers on worker loss, so overlapping
    # runs are a question of when rather than whether — and two of them would
    # both advance the watermark, leaving a window nobody pulled. This makes the
    # second an IntegrityError instead of a silent gap.
    op.create_index(
        "uq_sync_run_one_active",
        "sync_run",
        ["source", "entity"],
        unique=True,
        schema="ops",
        postgresql_where=sa.text("status = 'running'"),
    )

    # The watermark read and /v1/freshness are the same query: newest
    # successful run for this pair. Partial, so it holds roughly one row per
    # entity per sync rather than the whole table.
    op.create_index(
        "ix_sync_run_last_success",
        "sync_run",
        ["source", "entity", "finished_at"],
        unique=False,
        schema="ops",
        postgresql_where=sa.text("status = 'success'"),
    )

    op.create_index(
        "ix_sync_run_started_at", "sync_run", ["started_at"], unique=False, schema="ops"
    )


def downgrade() -> None:
    # Unlike the baseline this one is genuinely reversible: dropping the table
    # loses the sync history, not the warehouse. Indexes and check constraints
    # go with it.
    op.drop_index("ix_sync_run_started_at", table_name="sync_run", schema="ops")
    op.drop_index("ix_sync_run_last_success", table_name="sync_run", schema="ops")
    op.drop_index("uq_sync_run_one_active", table_name="sync_run", schema="ops")
    op.drop_table("sync_run", schema="ops")
