"""What counts as worth waking someone for.

Every rule is a query over `sync_run`, and two of them are readings of work that
already exists: staleness is what `platform_freshness` computes for the badge,
and repeated failure is precisely the condition `breaker_status` uses to stop
calling a source. Recomputing either here would create a second definition that
could drift from the one the dashboard shows — the same mistake the workbook
made with its nine unauditable KPI formulas.

Rules only *detect*. Whether anyone is told, and how often, is `notifier`'s
problem. Keeping them apart means the rules stay pure functions of the database
and can be tested by writing history and reading the verdict.

**A failing source suppresses its own entities' staleness alerts.** If the CRM
is down then its five entities are all going stale, and reporting six problems
where there is one buries the cause under its consequences. The source-level
alert is the actionable one; the staleness is what it looks like from the
dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lnd.config import get_settings
from lnd.models import (
    AlertKind,
    AlertSeverity,
    Entity,
    Source,
    SyncMode,
    SyncRun,
    SyncStatus,
)
from lnd.sync.breaker import BreakerState, breaker_status
from lnd.sync.freshness import platform_freshness


@dataclass(frozen=True)
class Alert:
    """One detected problem, with the evidence for it.

    `key` is the identity the throttle and the resolution logic both work from,
    so it must stay the same for as long as the problem persists — no
    timestamps, no counts, nothing that moves.
    """

    key: str
    kind: AlertKind
    severity: AlertSeverity
    title: str
    detail: str
    source: Source | None = None
    entity: Entity | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


def _failing_sources(session: Session, now: datetime) -> list[Alert]:
    """A source the breaker has given up on.

    Critical, and reported per source rather than per entity: the CRM being
    down is one fact, not five.
    """
    alerts: list[Alert] = []

    for source in Source:
        status = breaker_status(session, source, now=now)
        if status.state is BreakerState.CLOSED:
            continue

        alerts.append(
            Alert(
                key=f"source_failing:{source}",
                kind=AlertKind.SOURCE_FAILING,
                severity=AlertSeverity.CRITICAL,
                title=f"{source} is not responding",
                detail=(
                    f"{status.consecutive_failures} consecutive failed syncs. "
                    f"Last error: {status.last_error_type or 'unknown'}. "
                    f"The platform is serving the last good data."
                ),
                source=source,
                evidence=status.as_details(),
            )
        )
    return alerts


def _stale_or_missing_data(
    session: Session, now: datetime, failing_sources: set[Source]
) -> list[Alert]:
    """Data that is behind, or that has never arrived.

    Read from the same `platform_freshness` the badge uses, so the alert and
    the screen can never disagree about whether something is stale.
    """
    alerts: list[Alert] = []
    report = platform_freshness(session, now=now)

    for source_report in report.sources:
        if source_report.source in failing_sources:
            # The cause is already alerted. Reporting the consequence too would
            # bury it.
            continue

        for entity in source_report.entities:
            if entity.status == "stale":
                lag_minutes = int((entity.lag_seconds or 0) // 60)
                alerts.append(
                    Alert(
                        key=f"data_stale:{entity.source}:{entity.entity}",
                        kind=AlertKind.DATA_STALE,
                        severity=AlertSeverity.WARNING,
                        title=f"{entity.source} {entity.entity} is {lag_minutes} minutes behind",
                        detail=(
                            f"Last successful sync {entity.last_success_at}. "
                            f"Last attempt was {entity.last_attempt_status}."
                        ),
                        source=entity.source,
                        entity=entity.entity,
                        evidence={
                            "lag_seconds": entity.lag_seconds,
                            "last_attempt_status": str(entity.last_attempt_status)
                            if entity.last_attempt_status
                            else None,
                        },
                    )
                )
            elif entity.status == "never_synced":
                alerts.append(
                    Alert(
                        key=f"never_synced:{entity.source}:{entity.entity}",
                        kind=AlertKind.NEVER_SYNCED,
                        severity=AlertSeverity.WARNING,
                        title=f"{entity.source} {entity.entity} has never synced",
                        detail=(
                            "Expected by the sync catalogue but no successful run exists. "
                            "Before go-live this is the normal state."
                        ),
                        source=entity.source,
                        entity=entity.entity,
                        evidence={
                            "last_attempt_status": str(entity.last_attempt_status)
                            if entity.last_attempt_status
                            else None
                        },
                    )
                )
    return alerts


def _reconcile_deletes(session: Session, now: datetime) -> list[Alert]:
    """A nightly reconcile that soft-deleted more than it plausibly should.

    This is the "unexpected difference" the brief asks about. A reconcile
    removing a handful of records is routine housekeeping; one removing
    hundreds means either a genuine purge at source or a bug in what we asked
    for, and both want a human before the numbers are published.

    Keyed on the run, so each bad reconcile is its own alert, and windowed so
    it stops being current news rather than staying live for ever.
    """
    settings = get_settings()
    threshold = settings.alert_reconcile_delete_threshold
    since = now - timedelta(seconds=settings.alert_reconcile_window_seconds)

    runs = session.scalars(
        select(SyncRun).where(
            SyncRun.mode == SyncMode.FULL_RECONCILE,
            SyncRun.status == SyncStatus.SUCCESS,
            SyncRun.records_deleted > threshold,
            SyncRun.finished_at >= since,
        )
    ).all()

    return [
        Alert(
            key=f"reconcile_deletes:{run.id}",
            kind=AlertKind.RECONCILE_DELETES,
            severity=AlertSeverity.CRITICAL,
            title=(f"Reconcile removed {run.records_deleted} {run.source} {run.entity} records"),
            detail=(
                f"The nightly reconcile soft-deleted {run.records_deleted} records "
                f"against a threshold of {threshold}. Confirm they really were "
                f"removed at source before publishing figures."
            ),
            source=run.source,
            entity=run.entity,
            evidence={
                "sync_run_id": run.id,
                "records_deleted": run.records_deleted,
                "records_fetched": run.records_fetched,
                "threshold": threshold,
            },
        )
        for run in runs
    ]


def evaluate_alerts(session: Session, *, now: datetime | None = None) -> list[Alert]:
    """Every problem currently detectable, most severe first.

    `now` is injectable so a test can place an outage at a chosen age.
    """
    now = now or datetime.now(UTC)

    failing = _failing_sources(session, now)
    failing_sources = {alert.source for alert in failing if alert.source is not None}

    alerts = [
        *failing,
        *_stale_or_missing_data(session, now, failing_sources),
        *_reconcile_deletes(session, now),
    ]

    return sorted(
        alerts, key=lambda alert: (alert.severity is not AlertSeverity.CRITICAL, alert.key)
    )
