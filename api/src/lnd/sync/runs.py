"""Opening, stamping and closing a sync run.

Every pull from every source goes through `record_sync_run`. It is the only
thing that writes `ops.sync_run`, so the audit trail cannot be half-kept: a job
that forgets to record itself does not run at all, because the recorder is how
it gets its watermark.

Three decisions shape this module.

**The recorder owns its own transactions, separate from the work.** The
`running` row is inserted and committed before the body starts, and the closing
update is a second transaction. If it shared the caller's session, a rollback of
the data write would take the record of the failure with it — the one row you
most want to survive. It also means the `running` row is visible to other
workers immediately, which is what makes `uq_sync_run_one_active` able to block
an overlap, and what leaves something behind to reap when a worker is killed.

**Both timestamps come from the database.** `started_at` is a server default and
`finished_at` is set with `now()` rather than Python's clock. The app container
and the database container do not share a clock, and a run that finished a
millisecond before it started is rejected by
`ck_sync_run_finished_after_started` — a real outage caused by a drifting NTP
rather than by anything wrong with the sync.

**A run that did not succeed never writes a watermark.** The read query already
filters to successful runs, but declining to write the column at all means a
failure cannot advance the position even if a future query forgets that filter.
The same rule bars a backfill from writing one: loading Feb-Aug 2026 must not
drag the live position back half a year.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lnd.config import get_settings
from lnd.db import session_scope
from lnd.models import SyncEntity, SyncMode, SyncRun, SyncSource, SyncStatus, SyncTrigger

log = logging.getLogger(__name__)

# Modes that may move the live position. A backfill covers a window chosen by a
# human and must leave the watermark alone.
_ADVANCING_MODES = frozenset({SyncMode.INCREMENTAL, SyncMode.FULL_RECONCILE})

_ERROR_TYPE_LIMIT = 128
_ERROR_MESSAGE_LIMIT = 4000


class SyncSkipped(Exception):
    """Raised by a sync body to record the run as skipped rather than failed.

    The circuit breaker declining to call a source it believes is down is the
    intended use. Nothing was attempted, so it must not count towards the
    consecutive failures that opened the breaker. `record_sync_run` records the
    run and swallows this — a breaker doing its job is not a task failure and
    must not trigger a Celery retry.
    """


class SyncAlreadyRunning(RuntimeError):
    """Another run for this source and entity is still in flight.

    Raised in place of the raw IntegrityError from `uq_sync_run_one_active`, so
    a caller can tell "someone else has this" apart from "the database is
    broken" without inspecting constraint names.
    """


@dataclass
class SyncRunRecorder:
    """The handle the body of a sync writes its outcome to.

    Nothing here touches the database. The values are collected in memory and
    written once when the run closes, so a sync that fails midway still reports
    the counts it reached.
    """

    run_id: int
    source: SyncSource
    entity: SyncEntity
    mode: SyncMode
    watermark_from: datetime | None
    records_fetched: int = 0
    records_written: int = 0
    records_deleted: int = 0
    watermark_to: datetime | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def count(self, *, fetched: int = 0, written: int = 0, deleted: int = 0) -> None:
        """Add to the running totals.

        `fetched` is what the source returned, `written` what was new enough to
        append to raw, `deleted` what a reconcile found had vanished. The gap
        between fetched and written is the signal: if they are equal every run,
        the source's `updated_at` is not telling the truth.
        """
        if fetched < 0 or written < 0 or deleted < 0:
            raise ValueError("record counts are cumulative and cannot be negative")
        self.records_fetched += fetched
        self.records_written += written
        self.records_deleted += deleted

    def advance_to(self, position: datetime) -> None:
        """Set the position a successful run will resume from next time.

        Ignored unless the run succeeds, and unless the mode is one that may
        advance the watermark at all.
        """
        self.watermark_to = position

    def note(self, **fields: Any) -> None:
        """Attach anything structured worth having later: pages walked, HTTP
        statuses seen, the breaker state at the time."""
        self.details.update(fields)


def last_successful_run(session: Session, source: SyncSource, entity: SyncEntity) -> SyncRun | None:
    """The newest run that actually worked, or None if there has never been one.

    Backs both the watermark and `/v1/freshness`; `ix_sync_run_last_success` is
    the partial index that makes it a single index lookup.
    """
    return session.scalar(
        select(SyncRun)
        .where(
            SyncRun.source == source,
            SyncRun.entity == entity,
            SyncRun.status == SyncStatus.SUCCESS,
        )
        .order_by(SyncRun.finished_at.desc())
        .limit(1)
    )


def watermark_for(
    session: Session,
    source: SyncSource,
    entity: SyncEntity,
    *,
    overlap: timedelta | None = None,
) -> datetime | None:
    """Where the next incremental pull should resume from.

    None means this entity has never synced, which is how the first run knows
    to ask for everything rather than for nothing.
    """
    previous = last_successful_run(session, source, entity)
    if previous is None or previous.watermark_to is None:
        return None

    if overlap is None:
        overlap = timedelta(seconds=get_settings().sync_overlap_seconds)
    return previous.watermark_to - overlap


def reap_abandoned_runs(
    session: Session,
    *,
    older_than: timedelta,
    source: SyncSource | None = None,
    entity: SyncEntity | None = None,
) -> int:
    """Close out runs whose worker died, and return how many were closed.

    `uq_sync_run_one_active` is what stops two syncs of one entity overlapping,
    and the price of it is that a SIGKILLed worker leaves a `running` row that
    blocks the entity for good. This is that debt being paid. The threshold sits
    above Celery's hard time limit, so a slow run is never mistaken for a dead
    one.

    Recorded as `failed`, not `skipped`: the pull was attempted and its outcome
    is unknown, which is a failure. `finished_at` comes from `now()`, later than
    the `started_at` of anything old enough to qualify.
    """
    cutoff = datetime.now(UTC) - older_than

    conditions = [SyncRun.status == SyncStatus.RUNNING, SyncRun.started_at < cutoff]
    if source is not None:
        conditions.append(SyncRun.source == source)
    if entity is not None:
        conditions.append(SyncRun.entity == entity)

    # Session.execute returns a CursorResult for DML; the declared return type
    # is the broader Result, which carries no rowcount.
    result = cast(
        "CursorResult[Any]",
        session.execute(
            update(SyncRun)
            .where(*conditions)
            .values(
                status=SyncStatus.FAILED,
                finished_at=func.now(),
                error_type="AbandonedRun",
                error_message=(
                    "No worker closed this run within the abandoned threshold; "
                    "the process was most likely killed."
                ),
            )
        ),
    )
    reaped = result.rowcount or 0
    if reaped:
        log.warning(
            "reaped abandoned sync runs",
            extra={
                "event": "sync.run.reaped",
                "reaped": reaped,
                "older_than_seconds": older_than.total_seconds(),
                "source": source,
                "entity": entity,
            },
        )
    return reaped


def _current_task_id() -> str | None:
    """The Celery task id, when running inside one, for joining to the logs."""
    try:
        from celery import current_task

        request = getattr(current_task, "request", None)
        task_id = getattr(request, "id", None)
    except Exception:  # pragma: no cover - celery absent or not in a task
        return None
    return str(task_id) if task_id else None


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _open_run(
    source: SyncSource,
    entity: SyncEntity,
    mode: SyncMode,
    triggered_by: SyncTrigger,
    attempts: int,
    task_id: str | None,
) -> SyncRunRecorder:
    """Reap, read the watermark and insert the `running` row, then commit."""
    settings = get_settings()

    with session_scope() as session:
        reap_abandoned_runs(
            session,
            older_than=timedelta(seconds=settings.sync_abandoned_after_seconds),
            source=source,
            entity=entity,
        )

        # A full reconcile pulls everything by definition, so it has no window
        # to resume from; a backfill is given its window by the caller.
        watermark_from = (
            watermark_for(session, source, entity) if mode is SyncMode.INCREMENTAL else None
        )

        run = SyncRun(
            source=source,
            entity=entity,
            mode=mode,
            triggered_by=triggered_by,
            status=SyncStatus.RUNNING,
            watermark_from=watermark_from,
            attempts=attempts,
            task_id=task_id if task_id is not None else _current_task_id(),
        )
        session.add(run)
        try:
            session.flush()
        except IntegrityError as exc:
            if "uq_sync_run_one_active" in str(exc):
                raise SyncAlreadyRunning(f"a {source} {entity} sync is already in flight") from exc
            raise
        run_id = run.id

    log.info(
        "sync run started",
        extra={
            "event": "sync.run.started",
            "run_id": run_id,
            "source": source,
            "entity": entity,
            "mode": mode,
            "watermark_from": watermark_from,
        },
    )
    return SyncRunRecorder(
        run_id=run_id,
        source=source,
        entity=entity,
        mode=mode,
        watermark_from=watermark_from,
    )


def _close_run(
    recorder: SyncRunRecorder,
    status: SyncStatus,
    *,
    error_type: str | None = None,
    error_message: str | None = None,
) -> None:
    """Write the outcome in its own transaction.

    The watermark is written only for a successful run in an advancing mode —
    see the module docstring. Counts are written whatever happened, because a
    run that fetched 50 of 100 records before dying is worth being able to see.
    """
    may_advance = status is SyncStatus.SUCCESS and recorder.mode in _ADVANCING_MODES

    with session_scope() as session:
        session.execute(
            update(SyncRun)
            .where(SyncRun.id == recorder.run_id)
            .values(
                status=status,
                finished_at=func.now(),
                records_fetched=recorder.records_fetched,
                records_written=recorder.records_written,
                records_deleted=recorder.records_deleted,
                watermark_to=recorder.watermark_to if may_advance else None,
                error_type=_truncate(error_type, _ERROR_TYPE_LIMIT) if error_type else None,
                error_message=(
                    _truncate(error_message, _ERROR_MESSAGE_LIMIT) if error_message else None
                ),
                details=recorder.details or None,
            )
        )

    log.info(
        "sync run finished",
        extra={
            "event": "sync.run.finished",
            "run_id": recorder.run_id,
            "source": recorder.source,
            "entity": recorder.entity,
            "mode": recorder.mode,
            "status": status,
            "records_fetched": recorder.records_fetched,
            "records_written": recorder.records_written,
            "records_deleted": recorder.records_deleted,
            "watermark_to": recorder.watermark_to if may_advance else None,
            "error_type": error_type,
        },
    )


@contextmanager
def record_sync_run(
    source: SyncSource,
    entity: SyncEntity,
    mode: SyncMode,
    *,
    triggered_by: SyncTrigger = SyncTrigger.SCHEDULED,
    attempts: int = 1,
    task_id: str | None = None,
) -> Iterator[SyncRunRecorder]:
    """Run a sync with its audit row opened, stamped and closed around it.

        with record_sync_run(SyncSource.CRM, SyncEntity.PROGRAM,
                             SyncMode.INCREMENTAL) as run:
            page = client.programs(changed_since=run.watermark_from)
            run.count(fetched=len(page), written=append_to_raw(page))
            run.advance_to(page.high_water_mark)

    Raises `SyncAlreadyRunning` before the body runs if another worker holds
    this entity. Records `skipped` and returns quietly if the body raises
    `SyncSkipped`. Records `failed` and re-raises for anything else, so Celery
    still sees the failure and applies its retry policy.
    """
    recorder = _open_run(source, entity, mode, triggered_by, attempts, task_id)

    try:
        yield recorder
    except SyncSkipped as skipped:
        _close_run(
            recorder,
            SyncStatus.SKIPPED,
            error_type=type(skipped).__name__,
            error_message=str(skipped) or None,
        )
    except Exception as exc:
        _close_run(
            recorder,
            SyncStatus.FAILED,
            error_type=type(exc).__name__,
            error_message=str(exc) or None,
        )
        raise
    else:
        _close_run(recorder, SyncStatus.SUCCESS)
