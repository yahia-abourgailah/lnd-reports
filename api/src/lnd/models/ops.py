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
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
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


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    """Persist an enum's values, not its member names.

    Without this SQLAlchemy would store `CRM` rather than `crm`, and the column
    in the database would not match the string the API and the logs use.
    """
    return [member.value for member in enum_cls]


def _enum_column(enum_cls: type[StrEnum], name: str) -> SAEnum:
    """A VARCHAR constrained to the enum's values by a CHECK.

    `native_enum=False` on purpose. A native PostgreSQL enum type is a nuisance
    to extend — `ALTER TYPE ... ADD VALUE` has transaction restrictions that sit
    badly with migrations running as one transactional one-shot container. This
    renders as `VARCHAR(32) CHECK (col IN (...))`, which gives the same
    guarantee and turns adding a source into an ordinary constraint swap.

    The Python side still types as the enum, so mypy rejects a wrong string
    before the database ever sees it.
    """
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        length=32,
        values_callable=_enum_values,
    )


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
