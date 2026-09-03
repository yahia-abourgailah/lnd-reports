"""Detecting problems worth reporting, and reporting them exactly once.

    rules      what counts as a problem — pure queries over sync_run
    notifier   whether anyone is told, how often, and when it clears

Kept apart so the rules stay testable as functions of the database, and so a
second delivery channel is a new sink rather than a change to the detection.
"""

from __future__ import annotations

from lnd.alerts.notifier import AlertSink, DispatchResult, dispatch_alerts, log_sink
from lnd.alerts.rules import Alert, evaluate_alerts

__all__ = [
    "Alert",
    "AlertSink",
    "DispatchResult",
    "dispatch_alerts",
    "evaluate_alerts",
    "log_sink",
]
