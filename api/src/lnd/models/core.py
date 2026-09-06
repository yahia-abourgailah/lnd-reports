"""The star schema: dimensions and facts.

`core` is the only schema a report ever reads. It is a pure function of `raw`
plus `app` enrichment, so a fix to the transform replays over history without
re-querying the CRM — and, because `raw` is append-only, replaying is always
possible.

The dimensions arrive first because every fact keys on them. Facts follow at
three separate grains (enrollment, attendance, survey answer), which is the
direct fix for the workbook's central fault: one flat sheet carrying three
grains, where sums were only correct by convention (P-06).

Two things are deliberately not here. Nothing in `core` stores a *derived
metric* — NPS will be a query over `fact_survey_answer`, not a column — because
a stored metric is a second definition waiting to disagree with the first. And
nothing in `core` is nullable to mean "unknown but fine"; a row the transform
cannot key lands in `app.quarantine` instead, where somebody sees it.

`dim_program`, `dim_session`, `dim_trainer` and `dim_survey_question` are week 3
Person A. The three fact tables key on all four and cannot be built before them,
so they are not here either — a fact table foreign-keyed to a table that does
not exist is not a partial deliverable.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from lnd.db import SCHEMA_CORE, Base


class DimDate(Base):
    """One row per calendar day.

    A date dimension rather than `date_trunc` in every query, for one reason
    that matters here: the fiscal calendar. "Programs this quarter" has to mean
    the same thing in the dashboard, the monthly export and an ad-hoc query, and
    the only way to guarantee that is for all three to join to the same row
    rather than each re-deriving it.

    `date_key` is `YYYYMMDD` as an integer. It sorts and ranges identically to
    the date it encodes, and it makes a fact table's foreign key readable in a
    query result without a join — a small thing that saves a lot of joins while
    debugging.
    """

    __tablename__ = "dim_date"
    __table_args__ = (
        CheckConstraint("date_key = to_char(day, 'YYYYMMDD')::int", name="ck_dim_date_key_matches"),
        CheckConstraint("quarter BETWEEN 1 AND 4", name="ck_dim_date_quarter"),
        CheckConstraint("month BETWEEN 1 AND 12", name="ck_dim_date_month"),
        {"schema": SCHEMA_CORE},
    )

    date_key: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    day: Mapped[dt.date] = mapped_column(Date, nullable=False, unique=True)

    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    quarter: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    month: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    day_of_month: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 1=Monday
    week_of_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    month_name: Mapped[str] = mapped_column(String(16), nullable=False)
    month_abbr: Mapped[str] = mapped_column(String(4), nullable=False)
    #: `2026-02`. The label every monthly report groups by, stored rather than
    #: formatted per query so two reports cannot format it differently.
    year_month: Mapped[str] = mapped_column(String(7), nullable=False)

    #: The fiscal calendar, offset by FISCAL_YEAR_START_MONTH. Finance confirmed
    #: a January start, so these currently equal the calendar values — stored
    #: rather than assumed equal, so a future change is a rebuild of this column
    #: and not a rewrite of every query that grouped by `quarter` instead.
    fiscal_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    fiscal_quarter: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    #: Friday and Saturday in this region, not Saturday and Sunday — sessions
    #: are scheduled around the local week, so a hardcoded Sat/Sun would
    #: misreport "sessions delivered at the weekend" every time.
    is_weekend: Mapped[bool] = mapped_column(Boolean, nullable=False)


class DimEmployee(Base):
    """The workforce, versioned as SCD Type 2.

    One row per employee per period their attributes held. The business key is
    `employee_code` and the surrogate is `employee_key`; a fact joins the
    surrogate, so an employee who changes department keeps their history
    attached to the department they were in at the time rather than having it
    silently rewritten.

    **Why version at all**, when the CRM has no history: because from now on it
    does. The source returns today's roster and nothing else, so a department
    move is invisible the moment it happens — the old value is simply gone. This
    table is the only place that difference is ever recorded, and at 1,427 active
    employees the cost of keeping it is nothing.

    **`is_estimated`** is the honest half of that. Every period before the first
    snapshot is reconstructed from today's attributes, because there is no other
    source for it. A February report attributing a session to the department
    someone joined in August is not wrong by accident — it is the only answer
    available — but it must be labelled, and this is the label.
    """

    __tablename__ = "dim_employee"
    __table_args__ = (
        # The business key plus the version's start. A second version of the
        # same person beginning at the same instant is a transform bug, not
        # data — two snapshots of one moment disagreeing about someone.
        UniqueConstraint("employee_code", "valid_from", name="uq_dim_employee_code_from"),
        # Exactly one open version per person. A partial unique index rather
        # than a check, because the invariant is across rows: two current rows
        # for one employee_code would double every count that joins them, and
        # nothing downstream could detect it.
        Index(
            "uq_dim_employee_one_current",
            "employee_code",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        # Every headcount filters to the version live at a moment, so the range
        # columns lead. `company_name` trails because participation is reported
        # per company and that is the only breakdown applied before facts join.
        Index("ix_dim_employee_asof", "valid_from", "valid_to", "company_name"),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_dim_employee_period_ordered",
        ),
        # is_current and valid_to are two spellings of one fact, so they are
        # constrained to agree. Left free, a transform that closed a version but
        # forgot the flag would leave a row that is current by one column and
        # historical by the other, and every query would pick a different one.
        CheckConstraint(
            "(is_current AND valid_to IS NULL) OR (NOT is_current AND valid_to IS NOT NULL)",
            name="ck_dim_employee_current_is_open",
        ),
        CheckConstraint(
            "job_level_grade IS NULL OR job_level_grade > 0",
            name="ck_dim_employee_grade_positive",
        ),
        {"schema": SCHEMA_CORE},
    )

    employee_key: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    #: The business key. `TM4-3716`. Verified 1:1 with `odoo_id` across 2,218
    #: user objects — 417 people, 417 codes, none missing, no code carrying two
    #: names — which is what collapsed the BRD's four-step identity resolution
    #: to a join (P-07).
    employee_code: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The CRM's own id. Carried so a payload can be traced back without
    #: reversing the code, and because it is what `attendance[].user` keys on.
    odoo_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text)
    mobile: Mapped[str | None] = mapped_column(String(64))

    department_name: Mapped[str | None] = mapped_column(Text)
    department_odoo_id: Mapped[int | None] = mapped_column(BigInteger)
    #: Five companies today. This is P-13: the roster has to span every entity
    #: the CRM enrolls from, or participation rate is divided by the wrong
    #: population — which is exactly how the workbook came to divide five
    #: companies' attendance by one company's headcount.
    company_name: Mapped[str | None] = mapped_column(Text)
    company_odoo_id: Mapped[int | None] = mapped_column(BigInteger)

    #: Conformed on the way in, never on the way out (P-05).
    sector: Mapped[str | None] = mapped_column(Text)
    position_name: Mapped[str | None] = mapped_column(Text)
    job_level_name: Mapped[str | None] = mapped_column(Text)
    #: Integer, not the text the source sends, so `10` sorts after `9`.
    job_level_grade: Mapped[int | None] = mapped_column(SmallInteger)

    #: The source's own status. Trustworthy on a roster row and stale on one
    #: reconstructed from a program payload, which is why it is never the thing
    #: a denominator filters on by itself — see `on_current_roster`.
    status: Mapped[str | None] = mapped_column(String(32))

    #: Whether `get_users` still returns this person.
    #:
    #: The dimension has two sources and they answer different questions. The
    #: roster is who works here; the programs payload is who has ever trained,
    #: and 149 of the 419 people in it are not on the roster — 126 leavers plus
    #: 23 from two companies that no longer exist.
    #:
    #: Both belong here. Without the second group a third of every attendance
    #: figure would fail to key and quarantine, which is the numerator quietly
    #: losing a third of itself. With them and no flag, they would be counted in
    #: the denominator as current staff, which inflates headcount by people who
    #: left. So they are present, joinable, and excluded from headcount by this
    #: column rather than by a filter every query has to remember.
    on_current_roster: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    valid_from: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: True where the period predates the first snapshot, so its attributes are
    #: today's carried backwards rather than observed.
    is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    first_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
