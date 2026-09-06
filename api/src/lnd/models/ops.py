"""Operational tables: the record of what the pipeline did.

Nothing in `ops` is a fact about training. It is a fact about the machinery that
carries training data, which is why it sits in its own schema and why losing it
costs history rather than numbers.

`sync_run` is one row per attempt to pull one entity from one source. Four
separate obligations rest on that single row, and they are the reason it carries
more than a log line would:

  * **The watermark** (FR-A07). Where each entity got to is not stored anywhere
    else. It is read back as the `watermark_to` of the most recent successful
    run, so the audit trail and the scheduling position are the same fact and
    cannot disagree. A separate cursor table could claim to have caught up to
    14:00 with no successful 14:00 run to show for it.

  * **Freshness** (NFR-03). `/v1/freshness` answers "how stale is this?" from
    the newest successful run per entity — which is what `ix_sync_run_last_success`
    exists to make cheap. When a source is down the dashboard keeps serving the
    last known good data and this is what tells the user how old it is.

  * **Alerting.** Repeated failure, staleness past 60 minutes, and a reconcile
    that soft-deleted more than it plausibly should are all queries over this
    one table.

  * **Troubleshooting** (NFR-08). `task_id` joins a row here to the JSON log
    lines that worker emitted, so "why is February short 40 attendances" starts
    from a row and ends in the logs.

The grain is `(source, entity)`, not `source`. The CRM alone yields programs,
sessions, enrollments, attendance and evaluations, each with its own
`updated_at` and its own pace; one shared watermark would let a fast entity drag
a slow one backwards or skip it entirely. Keeping the pair is also what made
Q-03 costless to answer: evaluations turned out to come from the CRM rather than
Microsoft Forms, and that was a change to one tuple, not to this schema.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lnd.db import SCHEMA_OPS, Base

# `Source` and `Entity` are defined once, next to the raw table that stores
# them, and used unchanged here. They were briefly duplicated — this module
# carried its own SyncSource/SyncEntity — and the two spellings disagreed:
# `feedback` against `evaluation`, `course_activity` against
# `course_completion`, and a `forms` source that turned out not to exist. Two
# vocabularies for one set of facts is how a sync comes to report an entity
# under a name the raw layer has never heard of, so there is now one.
from lnd.ingest.models import Entity, Source
from lnd.models.columns import enum_column


class SyncMode(StrEnum):
    """Why the run happened, which decides how its result is read.

    Only `INCREMENTAL` and `FULL_RECONCILE` advance the watermark. `BACKFILL`
    loads a historical window on purpose and must not move the live position
    backwards — the Feb-Aug 2026 load in week 4 would otherwise cause every
    subsequent incremental to re-pull half a year.
    """

    INCREMENTAL = "incremental"
    FULL_RECONCILE = "full_reconcile"
    BACKFILL = "backfill"


class SyncStatus(StrEnum):
    """Terminal states all set `finished_at`; only `RUNNING` leaves it null.

    `SKIPPED` is the circuit breaker declining to call a source it believes is
    down. It is deliberately not `FAILED`: nothing was attempted, so it must not
    inflate the consecutive-failure count that opens the breaker in the first
    place, and it must not look like a source that started answering wrongly.
    """

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class SyncTrigger(StrEnum):
    """Beat, or a person pressing the button."""

    SCHEDULED = "scheduled"
    MANUAL = "manual"


# `_enum_column` used to be defined here. The star schema needed the same
# pattern in week 3, and two copies of it is how one of them comes to store
# `CRM` where the other stores `crm` — so it moved to `models/columns.py` and
# this alias keeps the local spelling.
_enum_column = enum_column


class SyncRun(Base):
    """One attempt to pull one entity from one source."""

    __tablename__ = "sync_run"
    __table_args__ = (
        # A finished run cannot finish before it started.
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_sync_run_finished_after_started",
        ),
        # Exactly the running rows are the unfinished ones. Both sides are NOT
        # NULL-safe, so this is total: a run cannot be recorded as successful
        # while still claiming to be in flight, and cannot sit `running` with a
        # finish time. It is what lets the reaper find abandoned runs by status
        # alone.
        CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name="ck_sync_run_terminal_is_finished",
        ),
        CheckConstraint(
            "records_fetched >= 0 AND records_written >= 0 AND records_deleted >= 0",
            name="ck_sync_run_counts_non_negative",
        ),
        CheckConstraint("attempts >= 1", name="ck_sync_run_attempts_positive"),
        # At most one run in flight per entity. Beat fires every 30 minutes and
        # `task_acks_late` redelivers on worker loss, so overlap is a question
        # of when, not whether. Two concurrent runs would both advance the
        # watermark and leave a silent gap between them; this makes the second
        # one an IntegrityError instead.
        #
        # The cost: a hard-killed worker leaves an orphaned `running` row that
        # blocks the entity until something clears it. The sync runner reaps
        # runs older than the Celery time limit before it starts a new one.
        Index(
            "uq_sync_run_one_active",
            "source",
            "entity",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
        # Serves both the watermark read and /v1/freshness: "newest successful
        # run for this pair". No DESC needed — PostgreSQL walks a btree
        # backwards at the same cost as forwards, and the partial predicate
        # keeps the index to roughly one row per entity per sync.
        Index(
            "ix_sync_run_last_success",
            "source",
            "entity",
            "finished_at",
            postgresql_where=text("status = 'success'"),
        ),
        # The operator's view: what has this thing been doing lately.
        Index("ix_sync_run_started_at", "started_at"),
        {"schema": SCHEMA_OPS},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # -- what ran -----------------------------------------------------------
    source: Mapped[Source] = mapped_column(_enum_column(Source, "sync_source"), nullable=False)
    entity: Mapped[Entity] = mapped_column(_enum_column(Entity, "sync_entity"), nullable=False)
    mode: Mapped[SyncMode] = mapped_column(_enum_column(SyncMode, "sync_mode"), nullable=False)
    triggered_by: Mapped[SyncTrigger] = mapped_column(
        _enum_column(SyncTrigger, "sync_trigger"),
        nullable=False,
        server_default=SyncTrigger.SCHEDULED.value,
    )
    status: Mapped[SyncStatus] = mapped_column(
        _enum_column(SyncStatus, "sync_status"), nullable=False
    )

    # -- when ---------------------------------------------------------------
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # -- the window this run covered ----------------------------------------
    # `watermark_from` is the previous successful position less the overlap the
    # sync applies for clock skew; `watermark_to` is the new position. Storing
    # both makes a run replayable from its own row — "which window did this
    # cover?" is answered without recomputing what the watermark was at the
    # time. Null on a full reconcile, which by definition has no window.
    watermark_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    watermark_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # -- what it moved ------------------------------------------------------
    # Three counters rather than one, because the difference between them is
    # the signal. `fetched - written` is how much came back unchanged, which is
    # the normal shape of an incremental run; a written count equal to fetched
    # every time means the source's `updated_at` is not to be trusted.
    # `deleted` is the reconcile soft-deleting what vanished at source, and a
    # spike in it is the "unexpected difference" worth waking someone for.
    records_fetched: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    records_written: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    records_deleted: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    # -- how it went --------------------------------------------------------
    # `attempts` is the retry number within the backoff policy, so "failed once
    # and recovered" is distinguishable from "failed four times running" without
    # correlating rows.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    # Split from the message so alerts can group by class of failure. A message
    # carries a timestamp or an id and is never twice the same string.
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- the thread back to the logs ----------------------------------------
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Anything structured and run-specific: pages walked, HTTP status seen, the
    # breaker state at the time. Deliberately loose — this is the field that
    # stops the next diagnostic need from becoming a migration.
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    @property
    def duration_seconds(self) -> float | None:
        """Wall time, or None while still running."""
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def is_finished(self) -> bool:
        return self.status is not SyncStatus.RUNNING

    def __repr__(self) -> str:
        return (
            f"<SyncRun {self.id} {self.source}/{self.entity} "
            f"{self.mode} {self.status} fetched={self.records_fetched}>"
        )


class AlertSeverity(StrEnum):
    WARNING = "warning"
    CRITICAL = "critical"


class AlertKind(StrEnum):
    """What kind of problem was detected. Stored so alerts can be grouped and
    counted without parsing the key."""

    SOURCE_FAILING = "source_failing"
    DATA_STALE = "data_stale"
    NEVER_SYNCED = "never_synced"
    RECONCILE_DELETES = "reconcile_deletes"


class AlertNotification(Base):
    """One ongoing problem, and the record of having said so.

    The alert *conditions* are derived, like the breaker: staleness comes from
    the freshness query and repeated failure from the breaker, both computed
    from `sync_run`. What cannot be derived is whether anyone has been told.
    That is the only reason this table exists.

    Without it a three-day outage sends a message every evaluation — 144 of
    them — and the practical result is that someone mutes the channel and the
    next real alert goes unread. An alerting system that cries wolf is worse
    than none.

    A row lives from the first time a problem is reported until it stops being
    detected, when `resolved_at` is set. Clearing on resolution is not a
    nicety: it is what lets the same problem alert again promptly if it returns
    after recovering, rather than being suppressed by a stale throttle.
    """

    __tablename__ = "alert_notification"
    __table_args__ = (
        # At most one live notification per problem. The partial predicate is
        # what allows the same key to recur through history once resolved.
        Index(
            "uq_alert_notification_live",
            "alert_key",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
        ),
        # The operator's view, and what the throttle reads.
        Index("ix_alert_notification_last_sent", "last_sent_at"),
        CheckConstraint("times_sent >= 1", name="ck_alert_notification_times_sent_positive"),
        CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= first_seen_at",
            name="ck_alert_notification_resolved_after_first_seen",
        ),
        {"schema": SCHEMA_OPS},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Stable identity for one problem, e.g. `data_stale:crm:program`. Dedupe
    # and resolution both key on this, so it must not embed anything that
    # changes while the problem persists — no timestamps, no counts.
    alert_key: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[AlertKind] = mapped_column(_enum_column(AlertKind, "alert_kind"), nullable=False)
    severity: Mapped[AlertSeverity] = mapped_column(
        _enum_column(AlertSeverity, "alert_severity"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)

    # Nullable: a failing *source* has no single entity, and a future rule may
    # have neither.
    source: Mapped[Source | None] = mapped_column(
        _enum_column(Source, "sync_source"), nullable=True
    )
    entity: Mapped[Entity | None] = mapped_column(
        _enum_column(Entity, "sync_entity"), nullable=True
    )

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    times_sent: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    # The evidence as it stood when last sent: lag, failure count, retry time.
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    @property
    def is_live(self) -> bool:
        return self.resolved_at is None

    def __repr__(self) -> str:
        state = "live" if self.is_live else "resolved"
        return f"<AlertNotification {self.alert_key} {self.severity} {state} x{self.times_sent}>"


class SourcePresence(Base):
    """Whether a source record still exists, and when it was last seen.

    `raw` is append-only, so "the CRM no longer returns this program" cannot be
    written there — and it is not a payload the source sent, it is an
    observation about a pull. It belongs with the sync audit.

    A vanished record keeps every raw version it ever had. Nothing is erased; it
    stops being present, and the week-3 transform soft-deletes from `core` on
    that basis. Only a full reconcile may set `is_present = false`: an
    incremental pass sees a subset by construction, and treating a subset as the
    whole world would soft-delete the catalogue on every run.
    """

    __tablename__ = "source_presence"
    __table_args__ = (
        UniqueConstraint("source", "entity", "source_id", name="uq_source_presence"),
        # Present rows have not vanished; absent rows have a time they went.
        # Total on both sides, so the pair cannot drift apart.
        CheckConstraint(
            "is_present = (vanished_at IS NULL)",
            name="ck_source_presence_absent_has_a_time",
        ),
        CheckConstraint(
            "last_seen_at >= first_seen_at",
            name="ck_source_presence_seen_order",
        ),
        # "What is still here?" — the transform's inclusion list, run every pass.
        Index("ix_source_presence_current", "source", "entity", "is_present"),
        {"schema": SCHEMA_OPS},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[Source] = mapped_column(_enum_column(Source, "sync_source"), nullable=False)
    entity: Mapped[Entity] = mapped_column(_enum_column(Entity, "sync_entity"), nullable=False)
    #: The natural key exactly as the source spells it, matching raw.source_record.
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Which run last saw it, so a disappearance is traceable to a pull.
    last_seen_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    is_present: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    vanished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        state = "present" if self.is_present else "vanished"
        return f"SourcePresence({self.source}/{self.entity}/{self.source_id} {state})"


class DqRule(StrEnum):
    """Which data-quality rule was violated (BRD §13, FR-F02).

    The governing rule of the whole platform: a record is either counted or
    registered as an exception, never neither. The workbook's most dangerous
    behaviour was silent loss — 38 attendees with no employee code simply
    vanished from sector reporting with no indication anything was missing —
    and every member of this enum is a case where the platform would otherwise
    do the same.

    The first eight are the BRD's set. The last three were added in week 3. Two
    of them followed from the live survey structure turning out to be
    per-program rather than fixed: a scored answer the platform cannot
    attribute to a metric is the same kind of silent loss, so it gets the same
    treatment. The third, ATTENDEE_OUTSIDE_ROSTER, followed from the shape of
    the payload — see its comment below.
    """

    IDENTITY_UNRESOLVED = "identity_unresolved"
    TRAINER_MISSING = "trainer_missing"
    CUSTOMISED_DEPT_MISSING = "customised_dept_missing"
    DURATION_UNDERIVABLE = "duration_underivable"
    ATTENDANCE_NO_ENROLLMENT = "attendance_no_enrollment"
    EVALUATION_NO_ATTENDANCE = "evaluation_no_attendance"
    DUPLICATE_ATTENDANCE = "duplicate_attendance"
    CAPACITY_EXCEEDED = "capacity_exceeded"
    SURVEY_QUESTION_UNMAPPED = "survey_question_unmapped"
    SURVEY_OPTION_UNSCORED = "survey_option_unscored"

    #: Somebody attended a session without appearing in the program's `users[]`
    #: roster at all, so the payload carries no `user` object for them — no
    #: sector, no department, no job level. They count in Total Participants and
    #: are absent from every coverage breakdown, which is the workbook's
    #: 38-attendee defect (P-07) arriving through a different door.
    #:
    #: Distinct from ATTENDANCE_NO_ENROLLMENT, which fires for a walk-in: that
    #: person is in the roster with `is_enrolled` false, so we can still say who
    #: they are. This one fires when we cannot.
    #:
    #: NOT the P-13 denominator question. "Is this attendee inside the
    #: enrollable population the participation rate divides by?" needs that
    #: population enumerated, which is Q-15 and still unanswered. When it is
    #: answered it gets its own rule rather than quietly widening this one.
    ATTENDEE_OUTSIDE_ROSTER = "attendee_outside_roster"


class DqDisposition(StrEnum):
    """What the platform did with the record — a different question from what
    a person should do about it.

    Two very different rules share this table. DURATION_UNDERIVABLE excludes a
    session from both hour metrics; CAPACITY_EXCEEDED excludes nothing and is
    reported as-is. A completeness indicator (FR-F04) that treated both as
    losses would understate the platform's coverage, and an operator triaging
    the queue needs to know which exceptions are actually costing numbers.
    """

    #: The record is excluded from the metrics the rule affects.
    QUARANTINED = "quarantined"
    #: The record counts; something about it is merely worth knowing.
    COUNTED = "counted"


class DqStatus(StrEnum):
    """Where the exception is in a human's workflow (FR-F03).

    `RESOLVED` is set by the transform, not by a person: an operator resolves
    an exception by authoring an enrichment value or an identity mapping, and
    the next transform pass finds the rule no longer violated and closes the
    row. That ordering matters — a row closed by hand while the underlying data
    still violates the rule would reopen on the next pass and look like a new
    problem.

    `DISMISSED` is the deliberate "yes, and that is fine", which the transform
    must respect rather than re-raise.
    """

    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class DqException(Base):
    """One open data-quality issue, keyed so it cannot be raised twice.

    Lives in `ops` rather than `core` because it is a fact about the pipeline's
    encounter with the data, not a fact about training — and because `core` is
    dropped and rebuilt, while a dismissal a person authored must survive that.

    `exception_key` is the whole design. The transform re-runs over the same
    programs on every pass, so a rule violated once is violated every time;
    without a stable key the queue would grow by a copy of itself each pass and
    a dismissal would last half an hour. The key names the *violation* — the
    rule plus the identifiers of the thing violating it — and never carries a
    timestamp, a count or a run id.
    """

    __tablename__ = "dq_exception"
    __table_args__ = (
        # One row per violation, for the life of the violation. Deliberately
        # not partial on status: a resolved exception that recurs should reopen
        # this same row and keep its history, and a dismissed one must not be
        # raisable again — which a partial index would allow.
        UniqueConstraint("exception_key", name="uq_dq_exception_key"),
        # The queue, in the order it is worked: FR-F01 lists issues by type and
        # age.
        Index("ix_dq_exception_open", "status", "rule", "first_seen_at"),
        # "Which exceptions affect this program's figures?" — what FR-D12's
        # per-view warning reads, so it has to stay cheap on every view.
        Index("ix_dq_exception_program", "crm_program_id"),
        Index("ix_dq_exception_last_seen", "last_seen_at"),
        CheckConstraint("occurrences >= 1", name="ck_dq_exception_occurrences_positive"),
        CheckConstraint("last_seen_at >= first_seen_at", name="ck_dq_exception_seen_ordered"),
        # Terminal states carry their timestamp; an open one cannot.
        CheckConstraint(
            "(status = 'open') = (closed_at IS NULL)", name="ck_dq_exception_closed_is_terminal"
        ),
        {"schema": SCHEMA_OPS},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    #: e.g. `identity_unresolved:crm:4821`, `duplicate_attendance:crm:88:4821`.
    exception_key: Mapped[str] = mapped_column(String(300), nullable=False)
    rule: Mapped[DqRule] = mapped_column(_enum_column(DqRule, "dq_rule"), nullable=False)
    disposition: Mapped[DqDisposition] = mapped_column(
        _enum_column(DqDisposition, "dq_disposition"), nullable=False
    )
    status: Mapped[DqStatus] = mapped_column(
        _enum_column(DqStatus, "dq_status"), nullable=False, server_default=DqStatus.OPEN.value
    )

    # -- what it is about ---------------------------------------------------
    # Nullable by design. A rule about a session, a rule about a person and a
    # rule about a program all belong in one queue, and a column per entity
    # would mean a migration for every new rule. These three are what the
    # interface filters and groups on; anything else goes in `details`.
    crm_program_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    crm_session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    employee_odoo_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: What an operator reads. Written once, at first sight, so the queue reads
    #: consistently rather than in whatever phrasing the latest pass used.
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    #: The evidence: the two spellings that failed to match, the duplicate's
    #: id, the question title with no mapping. What makes an exception
    #: actionable rather than merely visible.
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # -- its life -----------------------------------------------------------
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: Bumped by every pass that still finds the violation. The gap between
    #: this and now is how the queue knows a rule stopped firing.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Who dismissed it, and why. Null for a transform-resolved row, which had
    #: no human involved — and that difference is itself worth keeping.
    closed_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    closed_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def is_open(self) -> bool:
        return self.status is DqStatus.OPEN

    @property
    def costs_numbers(self) -> bool:
        """True when this exception is actually excluding records from metrics.

        What FR-D12's "how many records are excluded" counts, and what FR-F04's
        completeness indicator measures. A `COUNTED` exception is information,
        not a loss.
        """
        return self.is_open and self.disposition is DqDisposition.QUARANTINED

    def __repr__(self) -> str:
        return f"<DqException {self.rule} {self.status} x{self.occurrences} {self.exception_key}>"
