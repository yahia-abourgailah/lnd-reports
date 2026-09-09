"""Two more things the platform can tell somebody about.

Revision ID: 0014
Revises: 0013
Created: week 9

`alert_kind` is a VARCHAR with a CHECK rather than a native PostgreSQL enum, so
widening it is an ordinary constraint swap — no `ALTER TYPE ... ADD VALUE` and
no transaction restrictions. That was the reason for the choice in 0002 and
this is the third migration to collect on it.

`exclusions_ageing`   The exception console shows the queue, and somebody has
                      to look at it. This is what turns "somebody will notice"
                      into "somebody was told" when records have been missing
                      from figures for longer than a reporting cycle.

`report_undelivered`  The one week-9 failure that is otherwise silent by
                      construction. Nothing on any screen changes when a mail
                      does not arrive, and the person who would notice is the
                      one who did not receive it.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BEFORE = ("source_failing", "data_stale", "never_synced", "reconcile_deletes")
AFTER = (*BEFORE, "exclusions_ageing", "report_undelivered")


def _reset(values: Sequence[str]) -> None:
    allowed = ", ".join(f"'{value}'" for value in values)
    op.execute("ALTER TABLE ops.alert_notification DROP CONSTRAINT IF EXISTS alert_kind")
    op.execute(
        f"ALTER TABLE ops.alert_notification ADD CONSTRAINT alert_kind CHECK (kind IN ({allowed}))"
    )


def upgrade() -> None:
    _reset(AFTER)


def downgrade() -> None:
    # Any live notification of a kind being removed would violate the narrower
    # constraint. They are deleted rather than left to fail the migration: an
    # alert is a statement about the present, and the present after a rollback
    # is one where the platform cannot make that statement.
    op.execute(
        "DELETE FROM ops.alert_notification "
        "WHERE kind IN ('exclusions_ageing', 'report_undelivered')"
    )
    _reset(BEFORE)
