"""The star schema: `core`.

Everything here is a pure function of (raw + enrichment). No row in this schema
is authored by a person and no row survives a rebuild that the transform would
not produce again from `raw.source_record`. That is what makes "did the number
arrive wrong, or did we break it?" answerable: drop `core`, re-run the
transform, and if the figure changes the fault was ours.

The workbook's core structural fault is that one flat table carried three
different grains (P-06) — a program's duration, a person's attendance and a
person's feedback all in one row, so any sum over it double-counted something.
Three fact tables at three declared grains is the whole fix, and the grain of
each is written into a database constraint rather than into a comment.

WHICH KEY IS THE PRIMARY KEY, AND WHY IT DIFFERS PER TABLE

    dim_program   crm_program_id     the source's own integer id. Stable,
    dim_session   crm_session_id     never reused, and already the thing the
    dim_date      date_key           BRD says to key on (FR-B08). A surrogate
                                     here would buy nothing and cost a lookup
                                     on every fact write.

    dim_employee  employee_key       surrogate, because SCD Type 2 means the
    dim_trainer   trainer_key        natural key appears on several rows; and
                                     a trainer's natural key is *conformed
                                     text*, which can be corrected later. A
                                     fact pointing at text that gets
                                     recorrected is a fact that silently moves.

FACTS CARRY THE BUSINESS KEY AS WELL AS THE SURROGATE

Every fact holds `employee_odoo_id` — the durable identifier the source sent —
next to its nullable `employee_key`. Three things need that:

  * **Idempotency.** The unique constraint that defines each fact's grain is
    stated over source identifiers, so re-running the transform upserts rather
    than duplicating (FR-A10). A constraint over `employee_key` would let one
    person's two SCD versions produce two attendance rows for one scan.
  * **Quarantine without loss.** An attendee who resolves to nobody still gets
    a fact row, with `employee_key` null and a `dq_exception` raised. The
    workbook dropped 38 such people silently; that is P-07, and the fix is that
    the row exists and is *excluded by a join*, not by an absence.
  * **As-of correctness.** `employee_key` points at the version of the person
    current on the date of the event, not at whoever they are today.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from lnd.db import SCHEMA_CORE, Base
from lnd.models.columns import enum_column

# The CRM's own vocabulary, imported rather than restated. `sources.crm.models`
# defines these against the live API; a second copy here would be free to drift
# from what the source actually sends, which is how migration 0005 came to be
# needed in the first place.
from lnd.sources.crm.models import ProgramStatus, ProgramTarget, ProgramType


class ValueSource(StrEnum):
    """Where a conformed attribute's value came from.

    Enrichment is an overlay, never an edit (BRD section 7.3), and this column
    is what makes the overlay visible in the model. When the CRM later starts
    supplying a field L&D has been maintaining by hand, the transform prefers
    the CRM value and this flips from `override` to `crm` — which is exactly
    FR-C04, and needs no migration and no data fix.
    """

    CRM = "crm"
    OVERRIDE = "override"
    MISSING = "missing"


class IdentityStatus(StrEnum):
    """How an attendee's identity was established (FR-B04).

    The order is the resolution order, and it is recorded per fact rather than
    inferred, because "how confident are we that this attendance belongs to
    this person?" is a reporting question. A name match is a weaker claim than
    an employee-code match and the platform should be able to say so.

    `UNRESOLVED` is not an error state — it is a row that counts toward
    attendance and is excluded from anything requiring a person, with a
    `dq_exception` naming it. Counted or excepted, never neither.
    """

    ODOO_ID = "odoo_id"
    EMPLOYEE_CODE = "employee_code"
    EMAIL = "email"
    NORMALISED_NAME = "normalised_name"
    UNRESOLVED = "unresolved"


class NpsBand(StrEnum):
    """The three buckets a recommend score falls into.

    Stored rather than derived at query time for one reason: NPS must be
    aggregated by summing promoters and detractors and dividing *once* (P-03).
    Storing the band makes that a `COUNT(... WHERE band = 'promoter')` over a
    fact table, which cannot accidentally be averaged. A stored 0-10 score
    invites `AVG(score)`, which is the defect.
    """

    PROMOTER = "promoter"
    PASSIVE = "passive"
    DETRACTOR = "detractor"


class EvaluationDimension(StrEnum):
    """The five things a program's survey is asked to measure.

    Named by meaning, not by position. The workbook called them q1 to q5 and
    the BRD followed suit, which works only while every program shares one
    fixed survey. This CRM gives each program its own `survey` with its own
    `question_id`s, so position carries no meaning at all and `q3` would mean a
    different question in every program.

    The mapping from a program's question to one of these lives in
    `app.survey_question_map` — human-authored, like enrichment, because
    nothing in the payload identifies which question is which.
    """

    KNOWLEDGE_RELEVANCE = "knowledge_relevance"
    ACTIVITY_EFFECTIVENESS = "activity_effectiveness"
    LOGISTICS_EFFECTIVENESS = "logistics_effectiveness"
    FACILITATOR_PERFORMANCE = "facilitator_performance"
    RECOMMEND = "recommend"


# ---------------------------------------------------------------------------
# dimensions
# ---------------------------------------------------------------------------
class DimDate(Base):
    """The calendar. One row per day, generated, never synced.

    Exists so that "programs per month" is a group-by on a column rather than
    an expression over a timestamp, and — more importantly — so that a month
    with no training still appears in a trend with a zero. A trend built by
    grouping the facts themselves simply omits empty months, which is how a gap
    comes to look like a dip.
    """

    __tablename__ = "dim_date"
    __table_args__ = (
        CheckConstraint("month BETWEEN 1 AND 12", name="ck_dim_date_month_range"),
        CheckConstraint("quarter BETWEEN 1 AND 4", name="ck_dim_date_quarter_range"),
        Index("ix_dim_date_month_start", "month_start"),
        {"schema": SCHEMA_CORE},
    )

    date_key: Mapped[date] = mapped_column(Date, primary_key=True)

    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    quarter: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    month: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    #: "September 2026". Rendered once here rather than in every view, so the
    #: dashboard, the XLSX export and the PDF cannot spell a month differently.
    month_label: Mapped[str] = mapped_column(String(24), nullable=False)
    #: The first of the month. Every monthly rollup groups on this: it sorts
    #: correctly as a date, which `month_label` does not, and it joins to a
    #: month picker without parsing.
    month_start: Mapped[date] = mapped_column(Date, nullable=False)
    day_of_month: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    #: ISO: Monday is 1, Sunday is 7.
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_weekend: Mapped[bool] = mapped_column(Boolean, nullable=False)

    #: Calendar-aligned until someone states otherwise — see the week-3 open
    #: questions. Held as its own column rather than computed in views, so that
    #: a corrected fiscal calendar is a regeneration of this table and nothing
    #: else.
    fiscal_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    fiscal_quarter: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    def __repr__(self) -> str:
        return f"<DimDate {self.date_key}>"


class DimTrainer(Base):
    """One person who delivers training, after name variants are merged (P-04).

    The CRM stores a trainer as free text on the session — not as an entity —
    so `Ahmed Nasr`, `ahmed nasr` and `A. Nasr` arrive as three trainers, and
    the workbook reported three trainers' worth of NPS for one person. The alias
    table in `app` maps each observed spelling to one of these rows; this table
    holds only the canonical form.

    A surrogate key rather than the canonical name itself, because a canonical
    name is a *decision* and decisions get corrected. Facts pointing at text
    would move when someone fixes a spelling.
    """

    __tablename__ = "dim_trainer"
    __table_args__ = (
        UniqueConstraint("canonical_name", name="uq_dim_trainer_canonical_name"),
        {"schema": SCHEMA_CORE},
    )

    trainer_key: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: What the sort key is built from, so ordering ignores case and accents in
    #: the same way the alias matcher does.
    normalised_name: Mapped[str] = mapped_column(String(200), nullable=False)

    #: Not a person. `L&D Team` is what a session names when nobody recorded who
    #: delivered it, and it ranks sixth by sessions — above four named trainers.
    #: Marked rather than hidden: its sessions are real and its hours are in
    #: every total, so removing it would leave a figure that does not add up.
    is_placeholder: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    #: An outside vendor rather than a colleague. Their NPS is a fact about a
    #: supplier, which is a different question from how a facilitator did.
    is_external: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<DimTrainer {self.trainer_key} {self.canonical_name!r}>"


class DimEmployee(Base):
    """A person, versioned. Slowly Changing Dimension Type 2 (FR-B02).

    Participation Rate needs headcount *as of the period*, not today's. Without
    versioning, February's rate silently changes every time somebody joins or
    leaves — the same instability as the hardcoded 192 the platform exists to
    remove (P-01), just harder to notice. At a few hundred rows the versioning
    is free.

    KEYED ON `odoo_id`, NOT `employee_code`

    The BRD says `employee_code`. The live API disagrees: `user.odoo_id` is the
    identifier shared with HR and the only one present on every record, while
    `employee_code` is nullable and `user.id` is a CRM-local primary key that
    must never be joined on. Attendance rows reference `user_odoo_id` and
    nothing else — so keying on anything else would mean the join the whole
    coverage view depends on could not be made. `employee_code` is kept as an
    attribute and as the first identity-resolution probe (FR-B04).

    KNOWN GAP: THIS IS NOT A ROSTER

    Every row here comes from a `user` object nested inside a CRM program, so
    this table knows only people who touched some training. That is enough for
    every attribute-level breakdown and not enough for the two metrics that
    need a denominator over the whole company — Participation Rate and Coverage
    Gap — which count employees with *zero* attendance. Those stay
    unimplemented until a roster source is confirmed.
    """

    __tablename__ = "dim_employee"
    __table_args__ = (
        # The grain: one version of one person per validity window. Re-running
        # the transform over unchanged source data must not open a new version.
        UniqueConstraint("odoo_id", "valid_from", name="uq_dim_employee_version"),
        # Exactly one current version per person, enforced rather than trusted.
        # Two current rows would double that person in the headcount
        # denominator, which is the defect class this table exists to end.
        Index(
            "uq_dim_employee_current",
            "odoo_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        # Total, in both directions: the open version is the current one.
        CheckConstraint("is_current = (valid_to IS NULL)", name="ck_dim_employee_current_is_open"),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from", name="ck_dim_employee_window_ordered"
        ),
        # Total, so the flag cannot drift from the thing it describes: a version
        # is estimated exactly when its window starts before we observed it.
        # Nothing can be silently backdated without being labelled, and nothing
        # can be labelled without having been backdated.
        CheckConstraint(
            "is_estimated = (valid_from < observed_from)",
            name="ck_dim_employee_estimated_matches_backdating",
        ),
        # The as-of lookup: "who was this person on this date?"
        Index("ix_dim_employee_asof", "odoo_id", "valid_from", "valid_to"),
        # The identity-resolution probes, in resolution order.
        Index("ix_dim_employee_employee_code", "employee_code"),
        Index("ix_dim_employee_email", "email"),
        Index("ix_dim_employee_normalised_name", "normalised_name"),
        # The coverage view groups on this.
        Index("ix_dim_employee_sector", "sector"),
        {"schema": SCHEMA_CORE},
    )

    employee_key: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    #: The join key, as text: it arrives as an integer from some endpoints and
    #: a string from others, and one spelling has to win before the join.
    odoo_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # -- identity ----------------------------------------------------------
    employee_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: Case-folded, whitespace-collapsed, accent-stripped. The third identity
    #: probe (FR-B04) matches on this, never on `full_name`.
    normalised_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # -- attributes, all conformed (FR-B07) --------------------------------
    #: Trimmed. 940 of 1,052 live user objects carry a trailing space and 28
    #: distinct raw values collapse to 23 once trimmed — grouping on the raw
    #: value invents five sectors that do not exist. That is P-05.
    sector: Mapped[str | None] = mapped_column(String(120), nullable=True)
    department_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    position_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    job_level_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: Numeric so seniority orders correctly; as text "10" sorts before "9".
    job_level_grade: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # NFR-06: national ID and payscale are deliberately absent. No metric in
    # section 9 requires either, and ingesting them would widen the platform's
    # data-protection obligations for nothing. Their absence is the control.

    # -- the version window ------------------------------------------------
    #: When this version of the person is treated as having become true. For
    #: every version but the first this is observation time. The *first* version
    #: is backdated to cover history that predates our first sync — see
    #: `is_estimated` — because the alternative is that `as_of()` returns
    #: nothing for every session before the platform existed, which is all of
    #: them today.
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: When the CRM actually told us. Never backdated, so what was observed and
    #: what was assumed stay separable: for a moment at or after this, the
    #: version's attributes are something we saw; before it, they are an
    #: assumption we chose to make.
    observed_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: True when this version's window was extended backwards over time nobody
    #: observed. The source sends no change history and no effective dates, so
    #: a person's attributes before our first sync are not knowledge — they are
    #: today's values assumed to have held. That assumption is usually right
    #: and occasionally very wrong: somebody who transferred department in
    #: March is reported all the way back under the department they sit in now.
    #:
    #: Flagged rather than avoided because the alternative is worse. Refusing to
    #: answer would empty every breakdown of the history the CRM does hold
    #: (sessions back to September 2025), and inventing effective dates would
    #: make the as-of query look more precise than the data supports. So the
    #: figure is produced and labelled, and `headcount_as_of` carries the label
    #: through to the caller.
    is_estimated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    #: Hash of the attributes above. What decides whether a re-read of the same
    #: person opens a new version or changes nothing — the same mechanism, and
    #: for the same reason, as `raw.source_record.payload_hash`.
    #: Whether `get_users` still returns this person.
    #:
    #: The "KNOWN GAP" above is closed by this column. The dimension now has two
    #: sources: the roster, which is who works here, and the `user` objects
    #: nested in programs, which is who has ever trained. 149 of the 419 people
    #: in the second are not in the first — 126 leavers and 23 from two
    #: companies that no longer exist as separate entities.
    #:
    #: Both have to be here. Without the trained-but-departed a third of every
    #: attendance figure fails to key and is quarantined; without the flag they
    #: are counted as current staff and inflate the very denominator this table
    #: exists to make honest. So they are present, joinable, and excluded from
    #: headcount by a column rather than by a filter every query must remember.
    on_current_roster: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    attribute_hash: Mapped[str] = mapped_column(String(80), nullable=False)

    def __repr__(self) -> str:
        window = "current" if self.is_current else "closed"
        return f"<DimEmployee {self.employee_key} odoo={self.odoo_id} {window}>"


class DimProgram(Base):
    """A Learning Program. Keyed on the CRM id, never on the title (P-02).

    The direct fix for the workbook's most expensive defect: its pivots grouped
    by program title, so two separately-run "Hard Talks" programs merged into
    one row and one of them ceased to exist in every published figure. FR-B08
    states the rule; this primary key *is* the rule.
    """

    __tablename__ = "dim_program"
    __table_args__ = (
        Index("ix_dim_program_status", "computed_status"),
        Index("ix_dim_program_start_date", "start_date"),
        Index("ix_dim_program_title", "title"),
        CheckConstraint("capacity IS NULL OR capacity >= 0", name="ck_dim_program_capacity_sane"),
        {"schema": SCHEMA_CORE},
    )

    crm_program_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(300), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Each enum column names its own CHECK constraint. Two columns sharing a
    # constraint name is a duplicate-object error on the same table, and
    # `status` and `computed_status` are the same enum.
    #: What the CRM stores. Not recomputed as sessions pass, so it can still
    #: read `upcoming` after the last session has ended.
    status: Mapped[ProgramStatus | None] = mapped_column(
        enum_column(ProgramStatus, "ck_dim_program_status"), nullable=True
    )
    #: Derived by the CRM from the session dates. Total Programs counts on
    #: THIS, because it is the one that answers "is it finished?".
    computed_status: Mapped[ProgramStatus | None] = mapped_column(
        enum_column(ProgramStatus, "ck_dim_program_computed_status"), nullable=True
    )
    type: Mapped[ProgramType | None] = mapped_column(
        enum_column(ProgramType, "ck_dim_program_type"), nullable=True
    )
    target: Mapped[ProgramTarget | None] = mapped_column(
        enum_column(ProgramTarget, "ck_dim_program_target"), nullable=True
    )

    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    track_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    track_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    parent_program_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # -- the enrichment overlay, resolved -----------------------------------
    #: For a `department`-target program: which department it was built for.
    #: The CRM does record this (Q-02, answered), so the override is now a
    #: fallback rather than the only source — and `..._source` says which one
    #: won for this row without anyone having to diff two tables.
    customised_department_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    customised_department_source: Mapped[ValueSource] = mapped_column(
        enum_column(ValueSource, "ck_dim_program_department_source"),
        nullable=False,
        server_default=ValueSource.MISSING.value,
    )
    #: The program-level trainer. Sessions carry their own; this is the
    #: override L&D maintains for programs where no session names one.
    trainer_key: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA_CORE}.dim_trainer.trainer_key"), nullable=True
    )
    trainer_source: Mapped[ValueSource] = mapped_column(
        enum_column(ValueSource, "ck_dim_program_trainer_source"),
        nullable=False,
        server_default=ValueSource.MISSING.value,
    )

    # -- provenance ---------------------------------------------------------
    #: Set when the nightly reconcile finds the program gone from the source.
    #: A soft delete, because `raw` cannot forget it and neither should `core`:
    #: a program that vanishes from the CRM must vanish from *future* figures
    #: without retroactively changing a published month.
    deleted_at_source: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Which `raw.source_record` row this was built from — the drill-through
    #: from any published figure to the payload that produced it (NFR-07).
    raw_record_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    transformed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @property
    def counts_toward_total_programs(self) -> bool:
        """Total Programs counts completed, undeleted programs."""
        return self.computed_status is ProgramStatus.COMPLETED and self.deleted_at_source is None

    def __repr__(self) -> str:
        return f"<DimProgram {self.crm_program_id} {self.title!r}>"


class DimSession(Base):
    """One delivery of a program on one day. Child of `dim_program`.

    Session identity comes from `session.id` and nowhere else. The workbook's
    `#` column was never a session key — 117 distinct values over 415 rows, 99
    of them appearing once and 18 repeating — so its
    `COUNT(DISTINCT session_key)` was meaningless (P-12). Training Days counts
    on this primary key.
    """

    __tablename__ = "dim_session"
    __table_args__ = (
        Index("ix_dim_session_program", "crm_program_id"),
        Index("ix_dim_session_date", "session_date"),
        Index("ix_dim_session_trainer", "trainer_key"),
        # A negative duration would sum into Training Hours Delivered and
        # quietly *reduce* a published figure. The Pydantic model rejects an
        # end before its start at the boundary; this is the same guarantee one
        # layer lower, where a hand-written backfill also has to obey it.
        CheckConstraint(
            "duration_hours IS NULL OR duration_hours >= 0", name="ck_dim_session_duration_sane"
        ),
        # Total: a duration exists exactly when it was derivable. Nothing can
        # sit half-derived, so DURATION_UNDERIVABLE is a query on one boolean.
        CheckConstraint(
            "duration_derivable = (duration_hours IS NOT NULL)",
            name="ck_dim_session_duration_flag_matches",
        ),
        {"schema": SCHEMA_CORE},
    )

    crm_session_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    crm_program_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{SCHEMA_CORE}.dim_program.crm_program_id"), nullable=False
    )

    session_date: Mapped[date] = mapped_column(
        Date, ForeignKey(f"{SCHEMA_CORE}.dim_date.date_key"), nullable=False
    )
    session_time_from: Mapped[time | None] = mapped_column(Time, nullable=True)
    session_time_to: Mapped[time | None] = mapped_column(Time, nullable=True)

    #: Training Hours Delivered, derived from the times rather than typed
    #: (FR-B03). The workbook's 130.5 was hand-entered per row.
    duration_hours: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    #: False when either time is missing. Such a session is excluded from both
    #: hour metrics and raised as DURATION_UNDERIVABLE — counted or excepted,
    #: never neither.
    duration_derivable: Mapped[bool] = mapped_column(Boolean, nullable=False)

    trainer_key: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA_CORE}.dim_trainer.trainer_key"), nullable=True
    )
    #: The string the CRM actually sent, kept beside the conformed key. When
    #: someone asks why two trainers merged, this is the evidence.
    trainer_name_raw: Mapped[str | None] = mapped_column(String(200), nullable=True)
    location_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    deleted_at_source: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    raw_record_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    transformed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<DimSession {self.crm_session_id} program={self.crm_program_id}>"


# ---------------------------------------------------------------------------
# facts: three tables, three grains
# ---------------------------------------------------------------------------
class FactEnrollment(Base):
    """Grain: one employee x one program. Roughly 409 rows today.

    Only genuinely enrolled people. The CRM's `users[]` is the union of three
    groups — enrolled, walked in, or merely answered a survey — so treating the
    roster as the enrollment list would inflate the funnel's first step and
    understate No-show Rate. `is_enrolled` is checked before a row is written
    here; the others become attendance or evaluation facts with no enrollment,
    which is exactly what ATTENDANCE_NO_ENROLLMENT reports.
    """

    __tablename__ = "fact_enrollment"
    __table_args__ = (
        # The grain, in a constraint. Stated over source identifiers so a
        # re-run upserts (FR-A10), and so an unresolved person still gets one
        # row rather than none or two.
        UniqueConstraint("crm_program_id", "employee_odoo_id", name="uq_fact_enrollment_grain"),
        Index("ix_fact_enrollment_employee", "employee_key"),
        Index("ix_fact_enrollment_date", "enrolled_date"),
        {"schema": SCHEMA_CORE},
    )

    enrollment_key: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    crm_program_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{SCHEMA_CORE}.dim_program.crm_program_id"), nullable=False
    )
    employee_odoo_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Null when identity could not be resolved. The row still counts toward
    #: enrollment volume; it is excluded from any breakdown by person by the
    #: join failing, and a `dq_exception` says so (P-07, FR-B05).
    employee_key: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA_CORE}.dim_employee.employee_key"), nullable=True
    )
    identity_status: Mapped[IdentityStatus] = mapped_column(
        enum_column(IdentityStatus, "ck_fact_enrollment_identity_status"), nullable=False
    )

    enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The date half of `enrolled_at`, for the join to `dim_date`. Null when
    #: the source sent no enrollment timestamp, which happens.
    enrolled_date: Mapped[date | None] = mapped_column(
        Date, ForeignKey(f"{SCHEMA_CORE}.dim_date.date_key"), nullable=True
    )

    deleted_at_source: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    raw_record_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    transformed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<FactEnrollment program={self.crm_program_id} "
            f"odoo={self.employee_odoo_id} {self.identity_status}>"
        )


class FactAttendance(Base):
    """Grain: one employee x one session. 415 rows today.

    `learning_hours` is this fact's measure and it is *not* the same number as
    the session's duration. Summed, it gives Learner Hours — hours delivered
    multiplied by the people who received them, 1,386 in the workbook. Summing
    `dim_session.duration_hours` gives Training Hours Delivered, 130.5. The
    workbook tracked both and named neither, which is how they came to be
    quoted interchangeably.
    """

    __tablename__ = "fact_attendance"
    __table_args__ = (
        # The grain, and the DUPLICATE_ATTENDANCE guarantee in one constraint:
        # one person cannot be recorded present twice at one session. A double
        # QR scan is deduplicated to this row and the duplicate is raised as an
        # exception rather than counted.
        UniqueConstraint("crm_session_id", "employee_odoo_id", name="uq_fact_attendance_grain"),
        Index("ix_fact_attendance_program", "crm_program_id"),
        Index("ix_fact_attendance_employee", "employee_key"),
        Index("ix_fact_attendance_date", "attended_date"),
        Index("ix_fact_attendance_identity", "identity_status"),
        CheckConstraint(
            "learning_hours IS NULL OR learning_hours >= 0",
            name="ck_fact_attendance_hours_sane",
        ),
        {"schema": SCHEMA_CORE},
    )

    attendance_key: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    crm_session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{SCHEMA_CORE}.dim_session.crm_session_id"), nullable=False
    )
    #: Denormalised from the session on purpose. Every program-grain query —
    #: the scorecard, the funnel, Total Participants — would otherwise join
    #: through `dim_session` for a column that cannot change: a session never
    #: moves to another program.
    crm_program_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{SCHEMA_CORE}.dim_program.crm_program_id"), nullable=False
    )
    #: The retained source row's id, when the CRM gave one. Which of two
    #: duplicate scans survived is a question the exception queue will ask.
    crm_attendance_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    employee_odoo_id: Mapped[str] = mapped_column(String(64), nullable=False)
    employee_key: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA_CORE}.dim_employee.employee_key"), nullable=True
    )
    identity_status: Mapped[IdentityStatus] = mapped_column(
        enum_column(IdentityStatus, "ck_fact_attendance_identity_status"), nullable=False
    )

    attended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The session's date, not the scan's. A scan recorded at one minute past
    #: midnight belongs to the session it was for, and grouping on the scan
    #: timestamp would move it into the next day's figures.
    attended_date: Mapped[date] = mapped_column(
        Date, ForeignKey(f"{SCHEMA_CORE}.dim_date.date_key"), nullable=False
    )

    #: The session's duration, carried onto each attendee. Null exactly when
    #: the session's duration was underivable, so Learner Hours excludes it and
    #: DURATION_UNDERIVABLE accounts for it.
    learning_hours: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)

    deleted_at_source: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    raw_record_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    transformed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<FactAttendance session={self.crm_session_id} "
            f"odoo={self.employee_odoo_id} hours={self.learning_hours}>"
        )


class FactEvaluation(Base):
    """Grain: one response x one program. 77 rows today.

    THE FIVE MEASURES ARE NAMED, NOT NUMBERED

    The BRD specifies `q1` to `q5`, following the workbook. That works only
    while every program shares one fixed survey, and this CRM gives each
    program its own — its own `survey.id`, its own `question_id`s, its own
    option values. `q3` would mean a different question in every program, and a
    metric averaging "q3" across programs would be averaging unlike things with
    no way to notice.

    So the columns are named for what they measure, and the mapping from a
    program's question to one of them is human-authored in
    `app.survey_question_map`. An unmapped scored question lands in the
    exception queue rather than being guessed at.

    `nps_band` is stored, not the raw score's average. NPS is
    (promoters - detractors) / responses aggregated over the group, and the
    workbook averaged a per-row plus-or-minus 1 instead (P-03). Counting bands
    makes the correct aggregation the easy one and the defect unavailable.
    """

    __tablename__ = "fact_evaluation"
    __table_args__ = (
        # The grain: one response per person per program. A second submission
        # supersedes the first rather than adding a row — otherwise a keen
        # respondent weights their own program's NPS twice.
        UniqueConstraint("crm_program_id", "employee_odoo_id", name="uq_fact_evaluation_grain"),
        Index("ix_fact_evaluation_employee", "employee_key"),
        Index("ix_fact_evaluation_date", "responded_date"),
        Index("ix_fact_evaluation_nps_band", "nps_band"),
        # Every mapped quality measure is on the same 1-5 scale. A 7 in one of
        # these columns means a mapping claimed a 0-10 question was a 1-5 one,
        # and it must fail at the write rather than shift a published quality
        # percentage.
        CheckConstraint(
            " AND ".join(
                f"({column} IS NULL OR {column} BETWEEN 1 AND 5)"
                for column in (
                    "score_knowledge_relevance",
                    "score_activity_effectiveness",
                    "score_logistics_effectiveness",
                    "score_facilitator_performance",
                )
            ),
            name="ck_fact_evaluation_scores_in_range",
        ),
        CheckConstraint(
            "recommend_score IS NULL OR recommend_score BETWEEN 0 AND 10",
            name="ck_fact_evaluation_recommend_in_range",
        ),
        # Total: a band exists exactly when a score does. A banded row with no
        # score, or the reverse, would make the numerator and the denominator
        # of NPS disagree about how many responses there were.
        CheckConstraint(
            "(nps_band IS NULL) = (recommend_score IS NULL)",
            name="ck_fact_evaluation_band_matches_score",
        ),
        {"schema": SCHEMA_CORE},
    )

    evaluation_key: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    crm_program_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{SCHEMA_CORE}.dim_program.crm_program_id"), nullable=False
    )
    #: Which survey the answers came from, so a program whose survey was
    #: replaced mid-flight is diagnosable rather than merely inconsistent.
    crm_survey_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    employee_odoo_id: Mapped[str] = mapped_column(String(64), nullable=False)
    employee_key: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA_CORE}.dim_employee.employee_key"), nullable=True
    )
    identity_status: Mapped[IdentityStatus] = mapped_column(
        enum_column(IdentityStatus, "ck_fact_evaluation_identity_status"), nullable=False
    )

    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responded_date: Mapped[date | None] = mapped_column(
        Date, ForeignKey(f"{SCHEMA_CORE}.dim_date.date_key"), nullable=True
    )

    # -- the four quality dimensions, 1-5 ----------------------------------
    score_knowledge_relevance: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    score_activity_effectiveness: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    score_logistics_effectiveness: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    score_facilitator_performance: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    # -- the recommend question --------------------------------------------
    recommend_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    nps_band: Mapped[NpsBand | None] = mapped_column(
        enum_column(NpsBand, "ck_fact_evaluation_nps_band"), nullable=True
    )

    #: Every free-text answer, joined. The Program Scorecard prints all of
    #: them; nothing aggregates over this.
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: How many of this respondent's scored questions had no mapping. Non-zero
    #: means this response's quality scores are incomplete for a reason a
    #: person can fix, and the exception queue is holding the detail.
    unmapped_question_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )

    deleted_at_source: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    raw_record_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    transformed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<FactEvaluation program={self.crm_program_id} "
            f"odoo={self.employee_odoo_id} nps={self.nps_band}>"
        )
