"""Indexes derived from the dashboard's real queries.

Revision ID: 0010
Revises: 0009
Created: week 5

Week 3 indexed the columns the transform joins on. These are the ones the *read*
path filters on, which is a different set — taken from the queries
`lnd.metrics.scope` actually emits rather than from a guess about what a
dashboard might ask.

TWO PREDICATES APPEAR IN EVERY SINGLE METRIC

    deleted_at_source IS NULL
    identity_status <> 'unresolved'

They are constant: no request ever asks for soft-deleted rows or unresolved
identities, because a metric that counted them would be wrong rather than
differently scoped. A constant predicate is what a partial index is for — the
index holds only rows that can ever be counted, so it is smaller than the table
and the planner drops the filter entirely instead of evaluating it per row.

BREAKDOWN ATTRIBUTES

`sector` was already indexed; `department_name`, `company_name` and
`job_level_name` were not, and department is the widest breakdown on the
dashboard at 128 values — 128 slices, each a separate query. Partial on
`is_current` because every metric reads the current version of an employee.

HONEST ABOUT THE EFFECT TODAY

At 1,165 attendance rows PostgreSQL sequentially scans the table in under a
millisecond and will keep choosing to. These change nothing measurable now and
are not meant to: the BRD's ceiling is ~15,000 attendance rows across ten
companies, and the breakdown pattern issues one query per slice, so the read
path grows as rows x slices. Adding them at 1,165 rows costs nothing; adding
them at 15,000, after somebody notices the dashboard is slow, is a migration
under pressure.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CORE = "core"

#: The rows any metric may count. Identical in every fact table.
LIVE_ROWS = "deleted_at_source IS NULL AND identity_status <> 'unresolved'"

#: (index, table, columns) for the three fact grains. The employee key leads
#: because every metric either counts distinct employees or joins to them; the
#: program id follows because the funnel metrics pair the two.
FACT_INDEXES = (
    ("ix_fact_attendance_live", "fact_attendance", ["employee_key", "crm_program_id"]),
    ("ix_fact_enrollment_live", "fact_enrollment", ["employee_key", "crm_program_id"]),
    ("ix_fact_evaluation_live", "fact_evaluation", ["employee_key", "crm_program_id"]),
)

#: Breakdown attributes, on the current version only.
EMPLOYEE_INDEXES = (
    ("ix_dim_employee_department", "department_name"),
    ("ix_dim_employee_company", "company_name"),
    ("ix_dim_employee_job_level", "job_level_name"),
)


def upgrade() -> None:
    for name, table, columns in FACT_INDEXES:
        op.create_index(name, table, columns, schema=CORE, postgresql_where=sa.text(LIVE_ROWS))

    # Enrollment and evaluation are grouped by program for fill rate, no-show
    # and the per-program scorecard; only attendance had this.
    for name, table in (
        ("ix_fact_enrollment_program", "fact_enrollment"),
        ("ix_fact_evaluation_program", "fact_evaluation"),
    ):
        op.create_index(name, table, ["crm_program_id"], schema=CORE)

    for name, column in EMPLOYEE_INDEXES:
        op.create_index(
            name,
            "dim_employee",
            [column],
            schema=CORE,
            postgresql_where=sa.text("is_current"),
        )


def downgrade() -> None:
    for name, table, _ in FACT_INDEXES:
        op.drop_index(name, table_name=table, schema=CORE)
    op.drop_index("ix_fact_enrollment_program", table_name="fact_enrollment", schema=CORE)
    op.drop_index("ix_fact_evaluation_program", table_name="fact_evaluation", schema=CORE)
    for name, _ in EMPLOYEE_INDEXES:
        op.drop_index(name, table_name="dim_employee", schema=CORE)
