"""Celery application and the beat schedule.

Same image as the API, different command. The schedule is declared here so
there is one place to read what the platform does unattended:

    every 30 minutes   incremental sync on source updated_at   (FR-A07)
    nightly            full reconcile, catching deletes and    (FR-A08)
                       silent edits incremental cannot see
    monthly            generate and email the L&D report       (FR-E05)

Week 1 registers the schedule and a heartbeat only. The tasks themselves land
in weeks 2, 3 and 9; each entry points at a stub that logs and returns, so beat
is exercised end to end from day one rather than first switched on in week 9.
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
    },
)


@celery_app.task(name="lnd.heartbeat")
def heartbeat() -> dict[str, str]:
    """Proves the broker, the worker and this image are wired together."""
    log.info("heartbeat", extra={"event": "worker.heartbeat"})
    return {"status": "ok"}


@celery_app.task(name="lnd.sync.incremental")
def sync_incremental() -> dict[str, str]:
    """Week 2. Incremental pull per source on updated_at, with a watermark."""
    log.info("incremental sync (not yet implemented)", extra={"event": "sync.incremental.stub"})
    return {"status": "not_implemented"}


@celery_app.task(name="lnd.sync.full_reconcile")
def sync_full_reconcile() -> dict[str, str]:
    """Week 2. Nightly full pull; soft-deletes what vanished at source."""
    log.info("full reconcile (not yet implemented)", extra={"event": "sync.full.stub"})
    return {"status": "not_implemented"}


@celery_app.task(name="lnd.reports.monthly")
def monthly_report() -> dict[str, str]:
    """Week 9. Generate the monthly XLSX and email it, with no human step."""
    log.info("monthly report (not yet implemented)", extra={"event": "reports.monthly.stub"})
    return {"status": "not_implemented"}
