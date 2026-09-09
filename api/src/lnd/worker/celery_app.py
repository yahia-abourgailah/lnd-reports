"""Celery application and the beat schedule.

Same image as the API, different command. The schedule is declared here so
there is one place to read what the platform does unattended:

    every 30 minutes   pull every program, land what changed    (FR-A07)
    nightly            the same pull, plus deletion reconcile   (FR-A08)
    monthly            generate and email the L&D report       (FR-E05)
    every 15 minutes   evaluate the alert rules and notify

Nothing here is a stub any more. The schedule was wired end to end in week 1
with tasks that logged and returned, so beat was exercised from the first day
rather than first switched on the week it mattered; each has since been
replaced by the real thing.
"""

from __future__ import annotations

import logging

from celery import Celery
from celery.schedules import crontab

from lnd.config import get_settings
from lnd.logging import configure_logging
from lnd.models import SyncMode

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
        # Five minutes behind the sync, so a pass reads what the sync it
        # follows has landed rather than racing it. They are separate tasks
        # rather than one chained job on purpose: the transform reads only
        # `raw`, so it must still run — and still produce a correct `core` —
        # on a morning when the CRM is down and no sync succeeded at all.
        "transform-core": {
            "task": "lnd.transform.core",
            "schedule": crontab(minute="5,35"),
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
def sync_incremental() -> dict[str, int]:
    """The scheduled 30-minute pass (FR-A07).

    Pulls what has changed since each entity's watermark and lands it in raw.
    Every entity is attempted; one already in flight, or one that fails, is
    logged and stepped over so a stuck entity never makes the others stale.
    """
    from lnd.sync.runner import configured_pullers, run_all, summarise

    return summarise(run_all(configured_pullers(), mode=SyncMode.INCREMENTAL))


@celery_app.task(name="lnd.sync.full_reconcile")
def sync_full_reconcile() -> dict[str, int]:
    """The nightly pass, which also reconciles deletions (FR-A08).

    Fetches identically to the incremental one — the CRM exposes no
    `updated_at` filter, so both ask for everything and change detection is by
    payload hash. The difference is that this pass may conclude a record has
    gone: anything present before and absent from a complete pull is marked
    vanished, and the count feeds the alert that watches for a reconcile
    removing more than it plausibly should.

    Nothing is erased. A vanished record keeps every raw version it ever had
    and simply stops being present, so the week-3 transform excludes it from
    core while the history stays available to the week-10 reconciliation.
    """
    from lnd.sync.runner import configured_pullers, run_all, summarise

    return summarise(run_all(configured_pullers(), mode=SyncMode.FULL_RECONCILE))


@celery_app.task(name="lnd.transform.core")
def transform_core() -> dict[str, int]:
    """Rebuild `core` from `raw` (FR-B01 to FR-B08).

    One transaction for the whole pass. A partial `core` — programs loaded,
    attendance missing — would halve every hour metric with nothing to say so,
    which is worse than serving the previous coherent version for another half
    hour.
    """
    from lnd.transform.runner import run_transform

    return run_transform().as_dict()


@celery_app.task(name="lnd.reports.monthly")
def monthly_report(year: int | None = None, month: int | None = None) -> dict[str, object]:
    """Generate the monthly report, keep it, and send it (FR-E05).

    In that order, and the order is the point: the edition is stored before any
    attempt to send, so a relay that is down costs a delivery rather than a
    report. The file stays downloadable and re-sendable holding the numbers as
    they were on the first of the month — regenerating it later would give the
    current answer for that month, which is a different document.

    One transaction. A run that stored the workbook and then failed rendering
    the PDF would leave a period half-published, and the next run would see an
    edition already there and skip.

    `year`/`month` default to the last complete month, which is what the
    first-of-the-month schedule means. They exist so a period can be re-run by
    hand from the worker after a relay outage.
    """
    from lnd.db import session_scope
    from lnd.delivery import monthly as report

    with session_scope() as session:
        return report.run(session, year=year, month=month).as_dict()


@celery_app.task(name="lnd.reports.resend")
def resend_report(year: int, month: int) -> dict[str, object]:
    """Send a period's stored editions again, without regenerating them.

    Not on the schedule — this is the operator action after a relay outage, and
    the runbook's one line. It exists as a task rather than as a script
    somebody writes each time because the wrong instinct after a failed send is
    to re-run the report, and that produces the *current* answer for that
    month rather than the one that was published.
    """
    from lnd.db import session_scope
    from lnd.delivery import monthly as report

    with session_scope() as session:
        return report.resend(session, year=year, month=month).as_dict()


@celery_app.task(name="lnd.alerts.evaluate")
def evaluate_alerts_task() -> dict[str, int]:
    """Detect problems and notify anything not already reported.

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
