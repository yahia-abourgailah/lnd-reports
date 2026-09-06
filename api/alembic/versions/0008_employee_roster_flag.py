"""core.dim_employee.on_current_roster.

Revision ID: 0008
Revises: 0007
Created: week 3

Closes the gap 0007's `dim_employee` docstring names: "THIS IS NOT A ROSTER".

That table was built from `user` objects nested inside programs, so it knew only
people who had touched some training — enough for every attribute breakdown, and
not enough for Participation Rate or Coverage Gap, which need a denominator over
the whole company. `get_users` arrived after that branch was cut and supplies it:
1,435 current staff rather than the 419 who happen to have attended something.

Two sources means two populations that do not agree. 149 of the 419 trained
people are not on the roster — 126 leavers and 23 from two companies that no
longer exist as separate entities. Both belong in the dimension. Without the
departed, a third of every attendance figure fails to key and is quarantined;
without this flag they are counted as current staff and inflate the very
denominator the table exists to make honest.

Defaulting to true is correct for the rows already there: everything loaded
before this point came from a source that could not distinguish the two, and the
first pass with a roster restates each person on the evidence.
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
    # Headcount filters on this before anything else, paired with the validity
    # window every as-of question narrows by.
    op.create_index(
        "ix_dim_employee_roster",
        "dim_employee",
        ["on_current_roster", "valid_from", "valid_to"],
        schema="core",
    )


def downgrade() -> None:
    op.drop_index("ix_dim_employee_roster", table_name="dim_employee", schema="core")
    op.drop_column("dim_employee", "on_current_roster", schema="core")
