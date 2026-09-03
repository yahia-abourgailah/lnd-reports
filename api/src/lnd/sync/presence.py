"""Which source records still exist, and which have vanished.

`raw` is append-only — the application role has SELECT and INSERT and no UPDATE
(migration 0001) — so "this program is no longer in the CRM" cannot be recorded
there. It is not a payload the source sent; it is an observation about a pull.
So it lives in `ops`, alongside the sync audit.

A vanished record keeps every raw version it ever had. Nothing is erased; it
simply stops being present, and the week-3 transform soft-deletes it from `core`
on that basis. That is what lets a reconciliation in week 10 say when a program
disappeared and which run noticed.

Only a full pass may conclude anything is absent. An incremental pass that could
narrow its query would see a subset by construction, and treating that subset as
the whole world would soft-delete most of the catalogue on every run.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from lnd.ingest.models import Entity, Source
from lnd.models.ops import SourcePresence

log = logging.getLogger(__name__)


def mark_seen(
    session: Session,
    *,
    source: Source,
    entity: Entity,
    source_ids: Sequence[str],
    seen_at: datetime,
    sync_run_id: int | None = None,
) -> int:
    """Record that these records exist right now.

    A record that returns after vanishing is present again — programs get
    unarchived, and a resurrection should not need manual intervention.
    """
    if not source_ids:
        return 0

    rows = [
        {
            "source": source,
            "entity": entity,
            "source_id": source_id,
            "first_seen_at": seen_at,
            "last_seen_at": seen_at,
            "last_seen_run_id": sync_run_id,
            "is_present": True,
            "vanished_at": None,
        }
        # dict.fromkeys rather than set(): a source can repeat an id within one
        # response, and the insert must not conflict with its own rows.
        for source_id in dict.fromkeys(source_ids)
    ]

    statement = insert(SourcePresence).values(rows)
    session.execute(
        statement.on_conflict_do_update(
            constraint="uq_source_presence",
            set_={
                # Monotonic in both directions, because runs do not arrive in
                # time order. A BACKFILL loads a historical window on purpose,
                # so its position is older than anything already recorded: it
                # should widen `first_seen_at` backwards and must never drag
                # `last_seen_at` back with it, which would both lose the real
                # last sighting and violate the seen-order constraint.
                "first_seen_at": func.least(
                    SourcePresence.first_seen_at, statement.excluded.first_seen_at
                ),
                "last_seen_at": func.greatest(
                    SourcePresence.last_seen_at, statement.excluded.last_seen_at
                ),
                "last_seen_run_id": statement.excluded.last_seen_run_id,
                "is_present": True,
                "vanished_at": None,
            },
        )
    )
    return len(rows)


def reconcile_absent(
    session: Session,
    *,
    source: Source,
    entity: Entity,
    seen_at: datetime,
) -> list[str]:
    """Mark everything not seen in this pass as vanished, and name it.

    Returns the source ids that went, rather than a count: the exception queue
    in week 9 needs to say *which* records disappeared, and an alert that says
    "31 programs vanished" without naming them is not actionable at 3am.

    Call only after a successful full pass. A failed pull must never reach here,
    or an outage would soft-delete the entire catalogue.
    """
    gone = list(
        session.execute(
            update(SourcePresence)
            .where(
                SourcePresence.source == source,
                SourcePresence.entity == entity,
                SourcePresence.is_present.is_(True),
                SourcePresence.last_seen_at < seen_at,
            )
            .values(is_present=False, vanished_at=seen_at)
            .returning(SourcePresence.source_id)
        ).scalars()
    )

    if gone:
        log.warning(
            "records vanished at source",
            extra={
                "event": "sync.vanished",
                "source": str(source),
                "entity": str(entity),
                "count": len(gone),
                # Bounded: a reconcile that removed hundreds is a defect to
                # investigate, not a list to page into a log line.
                "source_ids": gone[:50],
            },
        )
    return gone


def present_ids(session: Session, *, source: Source, entity: Entity) -> list[str]:
    """Source ids currently believed to exist. The transform's inclusion list."""
    return list(
        session.execute(
            select(SourcePresence.source_id).where(
                SourcePresence.source == source,
                SourcePresence.entity == entity,
                SourcePresence.is_present.is_(True),
            )
        ).scalars()
    )


def vanished(session: Session, *, source: Source, entity: Entity) -> list[SourcePresence]:
    """Records seen before and absent now, most recently gone first."""
    return list(
        session.execute(
            select(SourcePresence)
            .where(
                SourcePresence.source == source,
                SourcePresence.entity == entity,
                SourcePresence.is_present.is_(False),
            )
            .order_by(SourcePresence.vanished_at.desc())
        ).scalars()
    )


def now() -> datetime:
    return datetime.now(tz=UTC)
