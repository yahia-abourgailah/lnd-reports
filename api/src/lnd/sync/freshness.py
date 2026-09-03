"""How stale is the data, and how do we know.

NFR-03 lets a source outage degrade freshness but never availability: when the
CRM stops answering the dashboard keeps serving the last good data. That is only
honest if the screen also says how old the data is, which is what this computes.

Two questions are answered per entity, and they are not the same question:

    last_success_at   when we last successfully checked
    data_current_to   how far through the source's timeline that check got

A sync that ran two minutes ago but only pulled up to an hour-old watermark is
fresh by the first measure and stale by the second. `lag_seconds` is measured
from `last_success_at`, because that is the one the freshness badge is about —
whether the pipeline is running — and `data_current_to` is reported alongside so
the other question is answerable too.

`status` is deliberately about staleness only. An entity whose data is fresh but
whose most recent attempt failed still reads `ok`, with the failure visible in
`last_attempt_status`. Folding the two together would make the badge mean two
things at once and leave nobody able to act on it. Repeated-failure alerting is
a separate rule over the same table.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from lnd.config import get_settings
from lnd.models import SyncEntity, SyncRun, SyncSource, SyncStatus
from lnd.sync.catalogue import EXPECTED_ENTITIES, ordering_key

FreshnessStatus = Literal["ok", "stale", "never_synced"]

# Worst wins when rolling entities up into a source, and sources into the
# platform. Never having synced beats merely being late: no data at all is a
# bigger problem than old data, and it is the one an operator must act on.
_SEVERITY: dict[FreshnessStatus, int] = {"ok": 0, "stale": 1, "never_synced": 2}


def _worst(statuses: list[FreshnessStatus]) -> FreshnessStatus:
    if not statuses:
        return "never_synced"
    return max(statuses, key=lambda status: _SEVERITY[status])


class EntityFreshness(BaseModel):
    source: SyncSource
    entity: SyncEntity
    status: FreshnessStatus

    last_success_at: datetime | None = Field(
        default=None, description="When this entity last synced successfully."
    )
    data_current_to: datetime | None = Field(
        default=None,
        description="How far through the source's timeline that sync got — its watermark.",
    )
    lag_seconds: float | None = Field(
        default=None,
        description="Age of the last successful sync. Null if there has never been one.",
    )

    last_attempt_at: datetime | None = Field(
        default=None, description="When this entity was last tried, successfully or not."
    )
    last_attempt_status: SyncStatus | None = None
    in_flight: bool = Field(
        default=False, description="A sync of this entity is running right now."
    )


class SourceFreshness(BaseModel):
    source: SyncSource
    status: FreshnessStatus
    lag_seconds: float | None = Field(
        default=None, description="The worst lag among this source's entities."
    )
    entities: list[EntityFreshness]


class FreshnessResponse(BaseModel):
    generated_at: datetime
    status: FreshnessStatus
    stale_after_seconds: int = Field(
        description="The threshold this response's statuses were computed against, "
        "so the client does not hardcode it."
    )
    sources: list[SourceFreshness]


def _latest_per_pair(
    session: Session, *, successful_only: bool
) -> dict[tuple[SyncSource, SyncEntity], SyncRun]:
    """One row per (source, entity): the newest run, by DISTINCT ON.

    Ordered by `finished_at` for successes and `started_at` for attempts — a
    run still in flight has no finish time, and it is exactly the row we want
    when asking what was tried most recently.
    """
    order_column = SyncRun.finished_at if successful_only else SyncRun.started_at

    statement = select(SyncRun)
    if successful_only:
        statement = statement.where(SyncRun.status == SyncStatus.SUCCESS)
    statement = statement.distinct(SyncRun.source, SyncRun.entity).order_by(
        SyncRun.source, SyncRun.entity, order_column.desc()
    )

    return {(run.source, run.entity): run for run in session.scalars(statement)}


def _entity_freshness(
    source: SyncSource,
    entity: SyncEntity,
    last_success: SyncRun | None,
    last_attempt: SyncRun | None,
    now: datetime,
    stale_after_seconds: int,
) -> EntityFreshness:
    lag_seconds: float | None = None
    status: FreshnessStatus = "never_synced"

    if last_success is not None and last_success.finished_at is not None:
        lag_seconds = (now - last_success.finished_at).total_seconds()
        status = "stale" if lag_seconds >= stale_after_seconds else "ok"

    return EntityFreshness(
        source=source,
        entity=entity,
        status=status,
        last_success_at=last_success.finished_at if last_success else None,
        data_current_to=last_success.watermark_to if last_success else None,
        lag_seconds=lag_seconds,
        last_attempt_at=last_attempt.started_at if last_attempt else None,
        last_attempt_status=last_attempt.status if last_attempt else None,
        in_flight=last_attempt is not None and last_attempt.status is SyncStatus.RUNNING,
    )


def platform_freshness(
    session: Session,
    *,
    now: datetime | None = None,
    stale_after_seconds: int | None = None,
) -> FreshnessResponse:
    """Freshness for every entity the platform should be syncing, grouped by source.

    `now` and `stale_after_seconds` are injectable so a test can place a run at
    a chosen age without sleeping.
    """
    now = now or datetime.now(UTC)
    if stale_after_seconds is None:
        stale_after_seconds = get_settings().freshness_stale_after_seconds

    successes = _latest_per_pair(session, successful_only=True)
    attempts = _latest_per_pair(session, successful_only=False)

    # Declared, plus anything with history that is not declared — so an entity
    # syncing outside the catalogue is reported rather than silently dropped.
    pairs = sorted(set(EXPECTED_ENTITIES) | set(attempts) | set(successes), key=ordering_key)

    by_source: dict[SyncSource, list[EntityFreshness]] = {}
    for source, entity in pairs:
        by_source.setdefault(source, []).append(
            _entity_freshness(
                source,
                entity,
                successes.get((source, entity)),
                attempts.get((source, entity)),
                now,
                stale_after_seconds,
            )
        )

    sources = [
        SourceFreshness(
            source=source,
            status=_worst([entity.status for entity in entities]),
            # None sorts as unknown rather than as zero: an entity that has
            # never synced has no lag to report, and max() over the rest would
            # otherwise understate the source.
            lag_seconds=max(
                (entity.lag_seconds for entity in entities if entity.lag_seconds is not None),
                default=None,
            ),
            entities=entities,
        )
        for source, entities in by_source.items()
    ]

    return FreshnessResponse(
        generated_at=now,
        status=_worst([source.status for source in sources]),
        stale_after_seconds=stale_after_seconds,
        sources=sources,
    )
