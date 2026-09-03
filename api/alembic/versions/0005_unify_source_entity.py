"""One vocabulary for sources and entities, and a usable link from raw to ops.

Revision ID: 0005
Revises: 0004
Created: week 2

Two integration defects, both found when the week-2 halves met.

**The enums disagreed.** `ops.sync_run` and `ops.alert_notification` were built
against a set of values that assumed Microsoft Forms carried feedback, and spelt
two entities differently from the raw layer: `feedback` against `evaluation`,
`course_activity` against `course_completion`. Q-03 answered the first — the
CRM's Learning Program Dataset returns `survey`, `survey_answers[]` and
`assessment_answers[]` nested inside each program, so Forms is not a source. The
other two were simply two names for one thing. Two vocabularies for one set of
facts is how a sync comes to report an entity under a name the raw layer has
never heard of.

**`raw.source_record.sync_run_id` was a UUID.** It was written before
`ops.sync_run` existed, against a guess that its key would be a UUID; it is a
BigInteger. As it stood, no raw row could point at the run that fetched it —
which is the join the audit trail is for.

Both tables are empty at this revision, so the columns are rewritten rather than
migrated. The type change is expressed as drop-and-add for exactly that reason:
`USING` casts have no meaning between a UUID and a bigint, and pretending
otherwise would leave a migration that cannot be replayed on a populated
database without silently discarding the links.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCES = ("crm", "hris", "linkedin")
ENTITIES = (
    "program",
    "session",
    "enrollment",
    "attendance",
    "evaluation",
    "employee",
    "course_completion",
)

OLD_SOURCES = ("crm", "forms", "hris", "linkedin")
OLD_ENTITIES = (
    "program",
    "session",
    "enrollment",
    "attendance",
    "feedback",
    "employee",
    "course_activity",
)

# (table, constraint name, column)
DOMAINS = (
    ("sync_run", "sync_source", "source"),
    ("sync_run", "sync_entity", "entity"),
    ("alert_notification", "sync_source", "source"),
    ("alert_notification", "sync_entity", "entity"),
)


def _values_list(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _reset_domains(sources: Sequence[str], entities: Sequence[str]) -> None:
    """Swap every source/entity CHECK for the given value sets.

    A non-native enum is a VARCHAR with a CHECK, so widening or narrowing it is
    an ordinary constraint swap rather than the `ALTER TYPE ... ADD VALUE`
    dance a native PostgreSQL enum would demand. That was the reason for
    choosing it in 0002, and this is the migration that collects on it.
    """
    for table, constraint, column in DOMAINS:
        allowed = sources if column == "source" else entities
        op.execute(f"ALTER TABLE ops.{table} DROP CONSTRAINT IF EXISTS {constraint}")
        op.execute(
            f"ALTER TABLE ops.{table} ADD CONSTRAINT {constraint} "
            f"CHECK ({column} IN ({_values_list(allowed)}))"
        )


def upgrade() -> None:
    _reset_domains(SOURCES, ENTITIES)

    # raw.source_record.sync_run_id: uuid -> bigint, to match ops.sync_run.id.
    # No foreign key: `raw` must remain writable when `ops` is being maintained,
    # and a sync run that is later pruned from the audit must not cascade into
    # deleting the payloads it fetched.
    op.drop_column("source_record", "sync_run_id", schema="raw")
    op.add_column(
        "source_record",
        sa.Column("sync_run_id", sa.BigInteger(), nullable=True),
        schema="raw",
    )
    op.create_index(
        "ix_raw_record_sync_run",
        "source_record",
        ["sync_run_id"],
        unique=False,
        schema="raw",
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_raw_record_sync_run", table_name="source_record", schema="raw")
    op.drop_column("source_record", "sync_run_id", schema="raw")
    op.add_column(
        "source_record",
        sa.Column("sync_run_id", sa.dialects.postgresql.UUID(as_uuid=False), nullable=True),
        schema="raw",
    )
    op.create_index(
        "ix_raw_record_sync_run",
        "source_record",
        ["sync_run_id"],
        unique=False,
        schema="raw",
    )

    _reset_domains(OLD_SOURCES, OLD_ENTITIES)
