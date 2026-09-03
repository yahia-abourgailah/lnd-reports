"""Landing records in `raw`, and reading them back.

Two guarantees are established here, and both are asserted by tests rather than
promised in a comment:

    idempotency  landing the same payload twice writes one row. Celery retries
                 tasks on worker loss, so a sync that duplicated attendance on
                 retry would drift the numbers with nothing to show for it.

    replay       `current()` reconstructs the latest state of every record
                 with no call to any source. This is what makes "did the number
                 arrive wrong, or did we break it?" answerable in week 4.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from lnd.ingest.hashing import payload_hash
from lnd.ingest.models import Entity, RawRecord, Source

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LandingResult:
    """What a landing pass did. Reported per entity in the week-2 sync audit."""

    received: int
    landed: int
    unchanged: int

    def __post_init__(self) -> None:
        # The raw-layer counterpart of the transform invariant: nothing arrives
        # without being accounted for.
        assert self.received == self.landed + self.unchanged, (
            f"landing lost records: received {self.received}, "
            f"landed {self.landed}, unchanged {self.unchanged}"
        )


def land(
    session: Session,
    *,
    source: Source,
    entity: Entity,
    records: Iterable[tuple[str, dict[str, Any]]],
    sync_run_id: str | None = None,
) -> LandingResult:
    """Append records to `raw`, skipping any whose content is already stored.

    `records` is an iterable of (source_id, payload). The payload is written
    exactly as given — this function must never clean, coerce or reorder it.

    Idempotency comes from the database, not from a read-then-write check: the
    unique constraint on (source, entity, source_id, payload_hash) makes a
    repeated landing a no-op even when two workers run the same window at the
    same moment.
    """
    rows = [
        {
            "source": str(source),
            "entity": str(entity),
            "source_id": str(source_id),
            "payload": payload,
            "payload_hash": payload_hash(payload),
            "sync_run_id": sync_run_id,
        }
        for source_id, payload in records
    ]

    if not rows:
        return LandingResult(received=0, landed=0, unchanged=0)

    # A payload can legitimately appear twice within one page of source data
    # (duplicate scans, for instance). Collapse those before the insert so the
    # statement itself cannot conflict with its own rows.
    deduped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row["source"]),
            str(row["entity"]),
            str(row["source_id"]),
            str(row["payload_hash"]),
        )
        deduped.setdefault(key, row)

    statement = (
        insert(RawRecord)
        .values(list(deduped.values()))
        .on_conflict_do_nothing(constraint="uq_raw_record_content")
        .returning(RawRecord.id)
    )
    landed = len(session.execute(statement).scalars().all())

    result = LandingResult(
        received=len(rows),
        landed=landed,
        unchanged=len(rows) - landed,
    )
    log.info(
        "landed raw records",
        extra={
            "event": "ingest.landed",
            "source": str(source),
            "entity": str(entity),
            "received": result.received,
            "landed": result.landed,
            "unchanged": result.unchanged,
        },
    )
    return result


def current(
    session: Session,
    *,
    source: Source,
    entity: Entity,
    source_ids: Sequence[str] | None = None,
) -> list[RawRecord]:
    """The latest landed version of each record. Touches no source system.

    This is the replay path: everything the transform consumes comes from here,
    so a transform can be fixed and re-run over history without re-querying the
    CRM.
    """
    statement = (
        select(RawRecord)
        .where(RawRecord.source == str(source), RawRecord.entity == str(entity))
        .distinct(RawRecord.source, RawRecord.entity, RawRecord.source_id)
        .order_by(
            RawRecord.source,
            RawRecord.entity,
            RawRecord.source_id,
            RawRecord.fetched_at.desc(),
            RawRecord.id.desc(),
        )
    )
    if source_ids is not None:
        statement = statement.where(RawRecord.source_id.in_(list(source_ids)))

    return list(session.execute(statement).scalars().all())


def history(session: Session, *, source: Source, entity: Entity, source_id: str) -> list[RawRecord]:
    """Every version of one record, oldest first.

    The answer to "this figure changed — when, and what did the source say
    before?", which the workbook could never answer at all.
    """
    statement = (
        select(RawRecord)
        .where(
            RawRecord.source == str(source),
            RawRecord.entity == str(entity),
            RawRecord.source_id == source_id,
        )
        .order_by(RawRecord.fetched_at, RawRecord.id)
    )
    return list(session.execute(statement).scalars().all())
