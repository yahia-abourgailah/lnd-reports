"""SQLAlchemy models.

Importing this package registers every table on `Base.metadata`. That is what
`alembic/env.py` consults, so a model that is never imported is a model Alembic
believes has been deleted.

Models are grouped by the schema they live in. The star schema arrives in week 3
and is a dozen tables; keeping `ops` separate from `core` means the pipeline
work and the model work do not collide in one file on every branch.

    ops.py    sync_run, alert_notification, and from week 9 dq_exception
    core.py   the star schema                          (week 3)
    app_.py   enrichment overrides                     (week 6)

`Source` and `Entity` are re-exported for convenience but *defined* in
`lnd.ingest.models`, beside the raw table that stores them. They are the
platform's shared vocabulary rather than anything specific to `ops`, and there
is exactly one definition of them on purpose.
"""

from __future__ import annotations

from lnd.ingest.models import Entity, RawRecord, Source
from lnd.models.ops import (
    AlertKind,
    AlertNotification,
    AlertSeverity,
    SyncMode,
    SyncRun,
    SyncStatus,
    SyncTrigger,
)

__all__ = [
    "AlertKind",
    "AlertNotification",
    "AlertSeverity",
    "Entity",
    "RawRecord",
    "Source",
    "SyncMode",
    "SyncRun",
    "SyncStatus",
    "SyncTrigger",
]
