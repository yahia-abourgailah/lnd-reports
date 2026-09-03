"""Not calling a source that has stopped answering.

Backoff handles a blip inside one run. This handles the outage across runs: once
a source has failed enough times in a row, calling it every thirty minutes is
just waiting for a timeout on a schedule. Each attempt costs the sync window,
delays the entities behind it, and adds nothing anyone can read.

**The state is derived, not stored.** There is no Redis key and no breaker
table — the answer is a query over `sync_run`: how many failures in a row since
this source last succeeded. That makes the audit trail and the breaker the same
fact, so "why was this skipped?" is answerable from the row that skipped it, and
there is no cached state to disagree with history, expire at the wrong moment or
vanish on a restart. It costs one query of at most fifty rows per attempt, which
against ten syncs per half hour is nothing.

**The scope is the source, not the entity.** If the CRM is down then programs,
sessions, enrollments, attendance and feedback are all down, and five
independent breakers would each have to learn that separately — five timeouts
where one would do. Counting across the source and resetting on any success for
it also gets the converse right: one flaky entity among four healthy ones never
trips it, because the others keep succeeding.

**Skipped runs are not failures.** They are this module's own output. Counting
them would make the breaker self-latching: it opens, every subsequent run is
skipped, the skips count as failures, and it never closes again.

    closed      calls pass
    open        the cooldown has not elapsed; the run is recorded skipped
    half_open   the cooldown has elapsed; one call is allowed to prove it

There is no lock on the half-open trial. Several entities of the same source can
enter it in the same beat tick and each make a call, so a source that is still
down gets a handful of probes rather than exactly one. Guarding that would need
the shared state this module exists to avoid, for a saving of a few requests per
cooldown.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from lnd.config import get_settings
from lnd.db import session_scope
from lnd.models import SyncRun, SyncSource, SyncStatus
from lnd.sync.runs import SyncSkipped

log = logging.getLogger(__name__)

# Enough history to count a run of failures and no more. The threshold is a
# handful; fifty rows is a wide margin at no cost.
_HISTORY_LIMIT = 50


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class BreakerStatus:
    """Why the breaker is where it is, not just where it is.

    Carries the evidence so a skipped run's `details` can say what tripped it,
    and so alerting (task 6) and the freshness view can report it without
    recomputing.
    """

    source: SyncSource
    state: BreakerState
    consecutive_failures: int
    last_failure_at: datetime | None = None
    retry_at: datetime | None = None
    last_error_type: str | None = None

    @property
    def is_open(self) -> bool:
        return self.state is BreakerState.OPEN

    def as_details(self) -> dict[str, object]:
        return {
            "breaker_state": str(self.state),
            "consecutive_failures": self.consecutive_failures,
            "retry_at": self.retry_at.isoformat() if self.retry_at else None,
            "last_error_type": self.last_error_type,
        }


def breaker_status(
    session: Session,
    source: SyncSource,
    *,
    now: datetime | None = None,
    threshold: int | None = None,
    cooldown: timedelta | None = None,
) -> BreakerStatus:
    """Where the breaker stands for this source, computed from its history.

    `now`, `threshold` and `cooldown` are injectable so a test can place an
    outage at a chosen age without waiting one.
    """
    now = now or datetime.now(UTC)
    settings = get_settings()
    if threshold is None:
        threshold = settings.breaker_failure_threshold
    if cooldown is None:
        cooldown = timedelta(seconds=settings.breaker_cooldown_seconds)

    # Only decided runs. `running` has no outcome yet and `skipped` is this
    # module's own doing — see the module docstring on self-latching.
    recent = session.scalars(
        select(SyncRun)
        .where(
            SyncRun.source == source,
            SyncRun.status.in_((SyncStatus.SUCCESS, SyncStatus.FAILED)),
        )
        .order_by(SyncRun.finished_at.desc())
        .limit(_HISTORY_LIMIT)
    ).all()

    failures: list[SyncRun] = []
    for run in recent:
        if run.status is SyncStatus.SUCCESS:
            break
        failures.append(run)

    if len(failures) < threshold:
        return BreakerStatus(
            source=source,
            state=BreakerState.CLOSED,
            consecutive_failures=len(failures),
        )

    newest = failures[0]
    retry_at = (newest.finished_at + cooldown) if newest.finished_at else None
    state = BreakerState.OPEN if retry_at and now < retry_at else BreakerState.HALF_OPEN

    return BreakerStatus(
        source=source,
        state=state,
        consecutive_failures=len(failures),
        last_failure_at=newest.finished_at,
        retry_at=retry_at,
        last_error_type=newest.error_type,
    )


def check_breaker(source: SyncSource, *, now: datetime | None = None) -> BreakerStatus:
    """Raise `SyncSkipped` if this source is not to be called right now.

    Called from inside a `record_sync_run` block, so the skip is recorded
    against a real run rather than disappearing:

        with record_sync_run(CRM, PROGRAM, INCREMENTAL) as run:
            status = check_breaker(CRM)
            run.note(**status.as_details())
            ...

    Belongs in the shared sync runner rather than in each source client, so it
    is written once and no client can forget it.
    """
    with session_scope() as session:
        status = breaker_status(session, source, now=now)

    if status.is_open:
        log.warning(
            "circuit breaker open; not calling source",
            extra={
                "event": "sync.breaker.open",
                "source": source,
                "consecutive_failures": status.consecutive_failures,
                "retry_at": status.retry_at,
                "last_error_type": status.last_error_type,
            },
        )
        raise SyncSkipped(
            f"circuit breaker open for {source} after "
            f"{status.consecutive_failures} consecutive failures"
        )

    if status.state is BreakerState.HALF_OPEN:
        log.info(
            "circuit breaker half open; allowing a trial call",
            extra={
                "event": "sync.breaker.half_open",
                "source": source,
                "consecutive_failures": status.consecutive_failures,
            },
        )

    return status
