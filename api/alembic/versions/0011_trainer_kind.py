"""What kind of thing a `dim_trainer` row is.

Revision ID: 0011
Revises: 0010
Created: week 7

The trainer scorecard is the first screen that ranks trainers against each
other, and the moment it does, two of the fifteen rows stop being comparable
with the rest. `L&D Team` is not a person — it is what a session names when
nobody recorded who delivered it — and it currently sits sixth by sessions,
above four named trainers. `Belton Academy` is an outside vendor, so its NPS is
a fact about a supplier and not about a colleague's facilitation.

Both are kept and both are counted. Hiding them would make their sessions
unreachable and their hours vanish from a total that must still add up. What
they get instead is a column saying what they are, so a scorecard can mark them
and a reader can decide what the ranking means — which is a judgement a person
should make with the label in front of them, not one the platform should make
by dropping rows.

Nullable-free defaults: every existing row is a named person until the
transform says otherwise, and the next pass says otherwise.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CORE = "core"


def upgrade() -> None:
    op.add_column(
        "dim_trainer",
        sa.Column("is_placeholder", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        schema=CORE,
    )
    op.add_column(
        "dim_trainer",
        sa.Column("is_external", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        schema=CORE,
    )


def downgrade() -> None:
    op.drop_column("dim_trainer", "is_external", schema=CORE)
    op.drop_column("dim_trainer", "is_placeholder", schema=CORE)
