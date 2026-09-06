"""core.dim_employee.on_current_roster.

Revision ID: 0008
Revises: 0007
Created: week 3

`dim_employee` has two sources, not one.

`get_users` returns who works here — 1,430 people, and no position, email or
mobile for any of them. The programs payload carries a full `user` object for
everyone who has ever trained, including those fields, and including people the
roster no longer has: 149 of 419, being 126 leavers plus 23 from two companies
that no longer exist as separate entities.

Both have to be in the dimension. Leave the second group out and a third of
every attendance figure fails to key and quarantines — the numerator silently
losing a third of itself, which is the exact failure this pipeline exists to
prevent. Put them in unflagged and they are counted as current staff, inflating
the denominator with people who left.

Hence one boolean. Present and joinable for facts; excluded from headcount by a
column rather than by a filter every future query has to remember.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dim_employee",
        sa.Column(
            "on_current_roster",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
            comment="Whether get_users still returns this person. False for leavers.",
        ),
        schema="core",
    )
    # Headcount filters on it before anything else, and pairs it with the
    # as-of range that ix_dim_employee_asof already leads on.
    op.create_index(
        "ix_dim_employee_roster",
        "dim_employee",
        ["on_current_roster", "valid_from", "valid_to"],
        schema="core",
    )


def downgrade() -> None:
    op.drop_index("ix_dim_employee_roster", table_name="dim_employee", schema="core")
    op.drop_column("dim_employee", "on_current_roster", schema="core")
