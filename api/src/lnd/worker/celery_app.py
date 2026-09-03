"""Celery application and the beat schedule.

Same image as the API, different command. The schedule is declared here so
there is one place to read what the platform does unattended:

    every 30 minutes   pull every program, land what changed    (FR-A07)
    nightly            the same pull, plus deletion reconcile   (FR-A08)
    monthly            generate and email the L&D report       (FR-E05)

Each entry points at a stub that logs and returns, so beat is exercised end to
end from day one rather than first switched on in week 9. The sync
implementation is Person B's week-2 work and lives elsewhere.
"""

from __future__ import annotations

import logging

from celery import Celery
from celery.schedules import crontab

from lnd.config import get_settings
from lnd.logging import configure_logging

log = logging.getLogger(__name__)

settings = get_settings()
configure_logging(settings.log_level)

celery_app = Celery("lnd", broker=settings.redis_url, backend=settings.redis_url)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # A sync that outlives its window is a fault, not something to let run.
    task_time_limit=30 * 60,
    task_soft_time_limit=25 * 60,
    # Redelivery on worker loss is safe: every sync is idempotent by
    # construction, so re-running a window is a no-op (FR-A10).
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    result_expires=7 * 24 * 3600,
    beat_schedule={
        "sync-incremental": {
            "task": "lnd.sync.incremental",
            "schedule": crontab(minute="*/30"),
        },
        "sync-full-reconcile": {
            "task": "lnd.sync.full_reconcile",
            "schedule": crontab(hour="2", minute="15"),
        },
        "report-monthly": {
            "task": "lnd.reports.monthly",
            # 07:00 on the first of the month, after the nightly reconcile.
            "schedule": crontab(day_of_month="1", hour="7", minute="0"),
        },
        # More often than the 30-minute sync, so a failure is reported within a
        # quarter of an hour rather than waiting for the next pull. Repetition
        # is the notifier's job, not the schedule's — this may run as often as
        # is useful without multiplying messages.
        "alerts-evaluate": {
            "task": "lnd.alerts.evaluate",
            "schedule": crontab(minute="*/15"),
        },
    },
)


@celery_app.task(name="lnd.heartbeat")
def heartbeat() -> dict[str, str]:
    """Proves the broker, the worker and this image are wired together."""
    log.info("heartbeat", extra={"event": "worker.heartbeat"})
    return {"status": "ok"}


@celery_app.task(name="lnd.sync.incremental")
def sync_incremental() -> dict[str, str]:
    """The scheduled 30-minute pass (FR-A07)."""
    log.info("incremental sync (not yet implemented)", extra={"event": "sync.incremental.stub"})
    return {"status": "not_implemented"}


@celery_app.task(name="lnd.sync.full_reconcile")
def sync_full_reconcile() -> dict[str, str]:
    """The nightly pass, which also reconciles deletions (FR-A08)."""
    log.info("full reconcile (not yet implemented)", extra={"event": "sync.full.stub"})
    return {"status": "not_implemented"}


@celery_app.task(name="lnd.reports.monthly")
def monthly_report() -> dict[str, str]:
    """Week 9. Generate the monthly XLSX and email it, with no human step."""
    log.info("monthly report (not yet implemented)", extra={"event": "reports.monthly.stub"})
    return {"status": "not_implemented"}


@celery_app.task(name="lnd.alerts.evaluate")
def evaluate_alerts_task() -> dict[str, int]:
    """Detect problems and notify anything not already reported.

    Unlike the sync tasks above this is not a stub: every rule is a query over
    `sync_run`, so it does real work today and will keep doing it unchanged once
    the sources are connected.

    One transaction for the whole evaluation. A failure part way through records
    nothing rather than half-claiming to have notified.
    """
    from lnd.alerts import dispatch_alerts
    from lnd.db import session_scope

    with session_scope() as session:
        result = dispatch_alerts(session)

    return {
        "raised": len(result.raised),
        "repeated": len(result.repeated),
        "suppressed": len(result.suppressed),
        "resolved": len(result.resolved),
    }
