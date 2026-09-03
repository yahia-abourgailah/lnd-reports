"""The raw landing table.

Revision ID: 0002
Revises: 0001
Created: week 1, Person A

One append-only table for every source and every entity. `payload` holds what
the source sent, byte for byte; `payload_hash` fingerprints it.

The unique constraint on (source, entity, source_id, payload_hash) is what makes
ingestion idempotent. Re-fetching an unchanged record collides and is skipped;
a record the source has since edited hashes differently, so it lands as a new
row and the previous version survives. Nothing is ever overwritten — and by
migration 0001 the application role could not overwrite it if it tried.
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


def upgrade() -> None:
    op.create_table(
        "source_record",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("entity", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_hash", sa.String(length=80), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sync_run_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source", "entity", "source_id", "payload_hash", name="uq_raw_record_content"
        ),
        schema="raw",
        comment=(
            "Append-only landing zone. Payloads exactly as received from the CRM, "
            "Microsoft Graph, the HRIS and LinkedIn. Never cleaned, never mutated."
        ),
    )

    # "The current version of every program" — run by the transform on every pass.
    op.create_index(
        "ix_raw_record_current",
        "source_record",
        ["source", "entity", "source_id", "fetched_at"],
        schema="raw",
    )

    # "What arrived in this sync run?" — for the week-2 audit trail.
    op.create_index("ix_raw_record_sync_run", "source_record", ["sync_run_id"], schema="raw")

    # Migration 0001 set default privileges for future tables in `raw`, so this
    # table already carries SELECT and INSERT for lnd_app and nothing else. The
    # explicit grant below covers the sequence, which default privileges for
    # TABLES do not reach.
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA raw TO lnd_app")

    # Belt and braces: the whole point of this table is that it cannot be
    # rewritten, so state it here as well as in the default privileges.
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON raw.source_record FROM lnd_app")


def downgrade() -> None:
    op.drop_index("ix_raw_record_sync_run", table_name="source_record", schema="raw")
    op.drop_index("ix_raw_record_current", table_name="source_record", schema="raw")
    op.drop_table("source_record", schema="raw")
