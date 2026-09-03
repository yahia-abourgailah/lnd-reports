"""Running one sync, and running all of them.

Everything the week-2 machinery was built for meets here. A single pass is:

    check the breaker      don't call a source known to be down
    read the watermark     where the last good pull got to
    fetch, with backoff    repeat a blip, refuse a 401
    land into raw          append-only, deduplicated by content hash
    count and advance      what moved, and where to resume

and every one of those has a test of its own already. This module is the order
they happen in, plus one decision that is not obvious.

**The watermark is taken before the fetch, not after.** A pull that starts at
12:00 and finishes at 12:04 must resume from 12:00, because a record modified at
12:02 may or may not have been in the page already read. Advancing to 12:04
would step over it and nothing would ever notice. Resuming from 12:00 re-reads
four minutes, which costs nothing: landing is idempotent by content hash, so
re-reading an unchanged record writes no row at all. The overlap in
`sync_overlap_seconds` widens the same margin for clock skew between us and the
source.

**Landing commits in its own transaction, separately from the audit.** The two
must be able to fail independently: raw rows that landed are real whether or not
the run is later recorded as failed, and the record of a failure has to outlive
the transaction that failed. A single transaction spanning both would lose one
to protect the other.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from lnd.db import session_scope
from lnd.ingest.landing import land
from lnd.ingest.models import Entity, Source
from lnd.models import SyncMode, SyncStatus, SyncTrigger
from lnd.sync.backoff import honours_retryable_flag, retry
from lnd.sync.breaker import check_breaker
from lnd.sync.presence import mark_seen, reconcile_absent
from lnd.sync.pullers import Record, SourcePuller
from lnd.sync.runs import SyncAlreadyRunning, record_sync_run

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncSummary:
    """What one pass did, for the task result and the caller's logs."""

    run_id: int
    source: Source
    entity: Entity
    status: SyncStatus
    fetched: int = 0
    landed: int = 0
    unchanged: int = 0
    #: Records present before and absent now. Only a full pass sets this.
    deleted: int = 0

    @property
    def changed(self) -> bool:
        return self.landed > 0


def run_sync(
    puller: SourcePuller,
    *,
    mode: SyncMode = SyncMode.INCREMENTAL,
    triggered_by: SyncTrigger = SyncTrigger.SCHEDULED,
    now: datetime | None = None,
) -> SyncSummary:
    """One entity, from one source, recorded from start to finish.

    Raises `SyncAlreadyRunning` if another worker holds this entity, and
    re-raises whatever the source raised if the fetch fails for good — both
    after the attempt has been written down. A breaker that is open returns a
    summary with status `skipped` rather than raising, because a breaker doing
    its job is not a task failure.
    """
    completed = False

    with record_sync_run(puller.source, puller.entity, mode, triggered_by=triggered_by) as run:
        breaker = check_breaker(puller.source, now=now)
        run.note(**breaker.as_details())

        # Before the fetch, deliberately — see the module docstring.
        position = now or datetime.now(UTC)

        # A full reconcile asks for everything by definition; only an
        # incremental has a window to resume from.
        changed_since = run.watermark_from if mode is SyncMode.INCREMENTAL else None

        records: list[Record] = retry(
            lambda: list(puller.fetch(changed_since=changed_since)),
            retry_on=puller.transient_errors,
            retry_if=honours_retryable_flag,
            describe=f"{puller.source}.{puller.entity}",
        )

        gone: list[str] = []
        with session_scope() as session:
            landed = land(
                session,
                source=puller.source,
                entity=puller.entity,
                records=records,
                sync_run_id=run.run_id,
            )

            # Presence is recorded on every pass, so a record that returns after
            # vanishing is present again without anyone intervening.
            mark_seen(
                session,
                source=puller.source,
                entity=puller.entity,
                source_ids=[source_id for source_id, _ in records],
                seen_at=position,
                sync_run_id=run.run_id,
            )

            # Only a full pass may conclude anything is absent (FR-A08). An
            # incremental that narrowed its query sees a subset by construction,
            # and treating a subset as the whole world would soft-delete the
            # catalogue every half hour.
            if mode is SyncMode.FULL_RECONCILE:
                gone = reconcile_absent(
                    session,
                    source=puller.source,
                    entity=puller.entity,
                    seen_at=position,
                )

        run.count(fetched=landed.received, written=landed.landed, deleted=len(gone))
        run.note(
            unchanged=landed.unchanged,
            filtered=changed_since is not None,
            vanished=gone[:50] if gone else None,
        )
        run.advance_to(position)
        completed = True

    # The body runs to the end unless the breaker refused, and a real failure
    # re-raises rather than reaching here.
    if not completed:
        return SyncSummary(
            run_id=run.run_id,
            source=puller.source,
            entity=puller.entity,
            status=SyncStatus.SKIPPED,
        )

    return SyncSummary(
        run_id=run.run_id,
        source=puller.source,
        entity=puller.entity,
        status=SyncStatus.SUCCESS,
        fetched=run.records_fetched,
        landed=run.records_written,
        unchanged=run.records_fetched - run.records_written,
        deleted=run.records_deleted,
    )


def run_all(
    pullers: Sequence[SourcePuller],
    *,
    mode: SyncMode = SyncMode.INCREMENTAL,
    triggered_by: SyncTrigger = SyncTrigger.SCHEDULED,
    now: datetime | None = None,
) -> list[SyncSummary]:
    """Every entity in turn, where one entity's trouble is its own.

    A source that is down fails the first entity and the breaker skips the rest,
    which is the intended shape. But an entity already in flight, or one that
    fails outright, must not stop the entities behind it — a stuck attendance
    sync should never mean programs go stale as well.
    """
    summaries: list[SyncSummary] = []

    for puller in pullers:
        try:
            summaries.append(run_sync(puller, mode=mode, triggered_by=triggered_by, now=now))
        except SyncAlreadyRunning:
            log.info(
                "another worker holds this entity; leaving it alone",
                extra={
                    "event": "sync.already_running",
                    "source": str(puller.source),
                    "entity": str(puller.entity),
                },
            )
        except Exception:
            # Already recorded as failed, and already alertable. Logged here
            # only so the loop's own decision to continue is visible.
            log.warning(
                "entity failed; continuing with the rest",
                extra={
                    "event": "sync.entity_failed",
                    "source": str(puller.source),
                    "entity": str(puller.entity),
                },
                exc_info=True,
            )

    return summaries


def configured_pullers() -> list[SourcePuller]:
    """The pullers the platform can currently run.

    One entity so far. Sessions, enrollments, attendance and employees join this
    list as their clients arrive, and nothing else in this module changes when
    they do.
    """
    from lnd.sources.crm.client import CrmClient
    from lnd.sync.pullers import CrmProgramPuller

    return [CrmProgramPuller.from_settings(CrmClient())]


def summarise(summaries: Iterable[SyncSummary]) -> dict[str, int]:
    """Totals for a task result."""
    summaries = list(summaries)
    return {
        "entities": len(summaries),
        "fetched": sum(summary.fetched for summary in summaries),
        "landed": sum(summary.landed for summary in summaries),
        "unchanged": sum(summary.unchanged for summary in summaries),
        "deleted": sum(summary.deleted for summary in summaries),
        "skipped": sum(1 for summary in summaries if summary.status is SyncStatus.SKIPPED),
    }
