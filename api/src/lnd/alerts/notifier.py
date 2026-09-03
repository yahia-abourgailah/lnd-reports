"""Saying it once, saying it again if it persists, and saying when it stops.

The rules detect; this decides who hears about it. Three behaviours, and the
middle one is the whole reason the module exists:

**New problem — send.** A row is opened in `ops.alert_notification`.

**Same problem, still there — usually stay quiet.** Repeated only once every
`alert_renotify_seconds`. Evaluated every fifteen minutes, a three-day outage
would otherwise produce 288 messages; the practical result of that is a muted
channel and the next real alert going unread.

**Problem gone — say so, and clear the record.** Resolution is not a courtesy.
Clearing the row is what lets the same problem alert *promptly* if it returns:
without it, a source that fails, recovers, and fails again ten minutes later
would be silenced by a throttle left over from the first failure.

Delivery is a `sink`, and the default writes a structured log line. Sending
email is deliberately not wired in here: it is an outward-facing action, the
SMTP path belongs to the week-9 report delivery work, and a log line is what the
container runtime already collects and what monitoring already watches (NFR-08).
Adding a sink that posts to a webhook or sends mail is a function, not a
redesign.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from lnd.alerts.rules import Alert, evaluate_alerts
from lnd.config import get_settings
from lnd.models import AlertNotification, AlertSeverity

log = logging.getLogger(__name__)

AlertSink = Callable[[Alert, bool], None]


@dataclass(frozen=True)
class DispatchResult:
    """What the evaluation did, for the caller and the task log."""

    raised: list[str]
    repeated: list[str]
    suppressed: list[str]
    resolved: list[str]

    @property
    def sent(self) -> int:
        return len(self.raised) + len(self.repeated) + len(self.resolved)


def log_sink(alert: Alert, resolved: bool) -> None:
    """The default delivery: one structured line per notification.

    Severity maps to log level so an existing log-based monitor can route on it
    without understanding this module.
    """
    if resolved:
        log.info(
            "alert resolved",
            extra={
                "event": "alert.resolved",
                "alert_key": alert.key,
                "alert_kind": str(alert.kind),
                "title": alert.title,
            },
        )
        return

    level = logging.ERROR if alert.severity is AlertSeverity.CRITICAL else logging.WARNING
    log.log(
        level,
        alert.title,
        extra={
            "event": "alert.raised",
            "alert_key": alert.key,
            "alert_kind": str(alert.kind),
            "severity": str(alert.severity),
            "title": alert.title,
            "detail": alert.detail,
            "source": alert.source,
            "entity": alert.entity,
            **alert.evidence,
        },
    )


def _live_notifications(session: Session) -> dict[str, AlertNotification]:
    rows = session.scalars(
        select(AlertNotification).where(AlertNotification.resolved_at.is_(None))
    ).all()
    return {row.alert_key: row for row in rows}


def dispatch_alerts(
    session: Session,
    *,
    alerts: Iterable[Alert] | None = None,
    now: datetime | None = None,
    renotify_after: timedelta | None = None,
    sink: AlertSink = log_sink,
) -> DispatchResult:
    """Evaluate, compare against what is already known, and notify the difference.

    Writes through the caller's session and does not commit — the caller owns
    the transaction, so an evaluation that fails part way records nothing rather
    than half-claiming to have notified.

    `alerts`, `now` and `renotify_after` are injectable so a test can drive the
    throttle without waiting hours or constructing history for every rule.
    """
    now = now or datetime.now(UTC)
    if renotify_after is None:
        renotify_after = timedelta(seconds=get_settings().alert_renotify_seconds)

    current = list(alerts) if alerts is not None else evaluate_alerts(session, now=now)
    current_by_key = {alert.key: alert for alert in current}
    live = _live_notifications(session)

    raised: list[str] = []
    repeated: list[str] = []
    suppressed: list[str] = []

    for key, alert in current_by_key.items():
        existing = live.get(key)

        if existing is None:
            session.add(
                AlertNotification(
                    alert_key=alert.key,
                    kind=alert.kind,
                    severity=alert.severity,
                    title=alert.title,
                    source=alert.source,
                    entity=alert.entity,
                    first_seen_at=now,
                    last_sent_at=now,
                    times_sent=1,
                    details=alert.evidence or None,
                )
            )
            sink(alert, False)
            raised.append(key)
            continue

        if now - existing.last_sent_at >= renotify_after:
            existing.last_sent_at = now
            existing.times_sent += 1
            # Refreshed so the repeat carries current evidence — a lag that has
            # grown from one hour to three is the useful part of saying it again.
            existing.title = alert.title
            existing.details = alert.evidence or None
            sink(alert, False)
            repeated.append(key)
        else:
            suppressed.append(key)

    resolved: list[str] = []
    for key, existing in live.items():
        if key in current_by_key:
            continue
        existing.resolved_at = now
        sink(
            Alert(
                key=existing.alert_key,
                kind=existing.kind,
                severity=existing.severity,
                title=f"Resolved: {existing.title}",
                detail="No longer detected.",
                source=existing.source,
                entity=existing.entity,
            ),
            True,
        )
        resolved.append(key)

    session.flush()

    result = DispatchResult(
        raised=raised, repeated=repeated, suppressed=suppressed, resolved=resolved
    )
    log.info(
        "alerts evaluated",
        extra={
            "event": "alert.evaluated",
            "detected": len(current),
            "raised": len(raised),
            "repeated": len(repeated),
            "suppressed": len(suppressed),
            "resolved": len(resolved),
        },
    )
    return result
