"""ops.alert_notification — what has been alerted on, and when.

Revision ID: 0003
Revises: 0002
Created: week 2

The alert conditions themselves are derived from `sync_run` and stored nowhere.
What cannot be derived is whether anyone has been told, which is the whole
reason for this table: without it a three-day outage sends a message every
evaluation and the channel gets muted.

Enum values are written out literally rather than imported, for the reason given
in 0002: a migration is a frozen statement of the schema at this revision.

No grants — `0001` set ALTER DEFAULT PRIVILEGES on `ops` for `lnd_app`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def upgrade() -> None:
    op.create_table(
        "alert_notification",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        # Stable identity for one problem, e.g. `data_stale:crm:program`.
        sa.Column("alert_key", sa.String(length=200), nullable=False),
        sa.Column(
            "kind",
            _enum(
                "alert_kind",
                "source_failing",
                "data_stale",
                "never_synced",
                "reconcile_deletes",
            ),
            nullable=False,
        ),
        sa.Column("severity", _enum("alert_severity", "warning", "critical"), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        # Nullable: a failing source has no single entity.
        sa.Column(
            "source", _enum("sync_source", "crm", "forms", "hris", "linkedin"), nullable=True
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
            nullable=True,
        ),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("times_sent", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_alert_notification"),
        sa.CheckConstraint("times_sent >= 1", name="ck_alert_notification_times_sent_positive"),
        sa.CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= first_seen_at",
            name="ck_alert_notification_resolved_after_first_seen",
        ),
        schema="ops",
    )

    # At most one live notification per problem. Partial, so the same key may
    # recur through history once resolved — which is what lets a problem that
    # comes back alert promptly instead of being suppressed by a stale throttle.
    op.create_index(
        "uq_alert_notification_live",
        "alert_notification",
        ["alert_key"],
        unique=True,
        schema="ops",
        postgresql_where=sa.text("resolved_at IS NULL"),
    )

    op.create_index(
        "ix_alert_notification_last_sent",
        "alert_notification",
        ["last_sent_at"],
        unique=False,
        schema="ops",
    )


def downgrade() -> None:
    op.drop_index("ix_alert_notification_last_sent", table_name="alert_notification", schema="ops")
    op.drop_index("uq_alert_notification_live", table_name="alert_notification", schema="ops")
    op.drop_table("alert_notification", schema="ops")
