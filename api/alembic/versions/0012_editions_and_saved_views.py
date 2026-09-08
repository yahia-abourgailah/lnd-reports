"""Retained report editions, and named filter sets.

Revision ID: 0012
Revises: 0011
Created: week 8

Two tables that have nothing to do with each other except that both are things
the platform remembers on a person's behalf, and both were the "Should" items
week 8 left open.

`ops.export_edition` — WHAT WAS ACTUALLY SENT

A monthly report regenerated in October over August's window is not the file
that went out in September. Enrichment decisions, corrections and late CRM rows
all move it, and usually for the better — but "the report we sent" and "the
current answer for that month" are two different questions, and only one of them
can be recovered after the fact. So the bytes are kept.

They are kept *in the database* rather than on a volume because the file is
~40 KB and there are twelve a year: a megabyte annually, against a second thing
to back up and rehearse restoring on its own schedule. `pg_dump` already takes
this table.

Only the monthly report is retained. Ad-hoc downloads are not: a drill-through
of named attendees is generated for one question by one person, and storing
every one of them would accumulate a second copy of the roster in a table
nobody audits.

`app.saved_view` — THE ONE TABLE IN `app` WITHOUT AN AUDIT TRAIL

Everything else in this schema supersedes rather than updates, because
everything else changes a published figure and FR-C03 wants the prior value. A
saved view is a bookmark. It changes nobody's number, and a history of renames
would be weight in the schema that no question ever asks.

What it stores is the URL — path and query string — because the address bar is
already this application's filter state. Restoring a view is navigation rather
than deserialisation, so there is no second representation of a filter set that
could drift from the first.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OPS = "ops"
APP = "app"

EXPORT_KINDS = ("monthly_xlsx", "monthly_pdf")
EXPORT_TRIGGERS = ("manual", "scheduled")


def upgrade() -> None:
    op.create_table(
        "export_edition",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                *EXPORT_KINDS,
                name="export_kind",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "trigger",
            sa.Enum(
                *EXPORT_TRIGGERS,
                name="export_trigger",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        # The period covered, never the date generated. A June report made in
        # October is still the June report.
        sa.Column("period_year", sa.Integer(), nullable=False),
        sa.Column("period_month", sa.SmallInteger(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("filters_applied", sa.Text(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Null when the schedule made it. Naming a service account as the
        # author would make an audit trail that says something untrue.
        sa.Column("generated_by", sa.String(length=320), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        # A digest of the *figures*, not of the file. Hashing the bytes would
        # answer almost nothing: the generation timestamp is inside every
        # export, so two renderings of one unchanged month never match. What
        # somebody actually asks on seeing two rows for August is whether the
        # numbers moved, and that is what this answers — across formats too,
        # since the workbook and the PDF of one month share it.
        sa.Column("figures_sha256", sa.String(length=64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("period_month BETWEEN 1 AND 12", name="ck_export_edition_period_month"),
        sa.CheckConstraint("byte_size > 0", name="ck_export_edition_not_empty"),
        schema=OPS,
    )
    op.create_index(
        "ix_export_edition_recent", "export_edition", ["kind", "generated_at"], schema=OPS
    )
    op.create_index(
        "ix_export_edition_period",
        "export_edition",
        ["kind", "period_year", "period_month"],
        schema=OPS,
    )

    op.create_table(
        "saved_view",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("owner_email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("path", sa.String(length=200), nullable=False),
        sa.Column("query", sa.Text(), server_default="", nullable=False),
        sa.Column("describes", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        # Two views called "Q3" by one person is a mistake at the moment of
        # saving, not something to discover a month later.
        sa.UniqueConstraint("owner_email", "name", name="uq_saved_view_owner_name"),
        schema=APP,
    )
    op.create_index("ix_saved_view_owner", "saved_view", ["owner_email"], schema=APP)


def downgrade() -> None:
    op.drop_index("ix_saved_view_owner", table_name="saved_view", schema=APP)
    op.drop_table("saved_view", schema=APP)
    op.drop_index("ix_export_edition_period", table_name="export_edition", schema=OPS)
    op.drop_index("ix_export_edition_recent", table_name="export_edition", schema=OPS)
    op.drop_table("export_edition", schema=OPS)
