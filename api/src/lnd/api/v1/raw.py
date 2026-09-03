"""Browsing the raw landing layer.

The point of storing source payloads unmodified is that you can go and look at
them. This is that: what landed, when, what it said, and what it said before.

    GET /v1/raw/summary                             what is in there, by source
    GET /v1/raw/{source}/{entity}                   list current records
    GET /v1/raw/{source}/{entity}/{id}              one full payload
    GET /v1/raw/{source}/{entity}/{id}/history      every version of it

It is the ancestor of the drill-through in FR-D03 — the same idea one layer
down. Where drill-through will answer "which records produced this KPI?", this
answers "what did the source actually send us?", which is the question week 4
needs when a restated figure is challenged.

Everything here is read-only and authenticated. Payloads carry employee names,
emails and mobile numbers, so nothing is served anonymously.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lnd.auth.dependencies import CurrentUser
from lnd.db import get_db
from lnd.ingest import Entity, RawRecord, Source, current, history

router = APIRouter(prefix="/raw", tags=["raw"])

DbSession = Annotated[Session, Depends(get_db)]

#: A whole program tree is large. Lists carry a few identifying fields instead,
#: and the full payload is one explicit request away.
PREVIEW_KEYS = (
    "id",
    "title",
    "status",
    "computed_status",
    "type",
    "target",
    "start_date",
    "capacity",
    "name",
    "full_name",
    "email",
    "employee_code",
)


class EntityCount(BaseModel):
    source: str
    entity: str
    records: int = Field(description="Distinct records, not rows")
    versions: int = Field(description="Total stored versions, including superseded")
    first_seen: datetime | None
    last_seen: datetime | None


class RawSummary(BaseModel):
    total_versions: int
    entities: list[EntityCount]


class RecordSummary(BaseModel):
    source: str
    entity: str
    source_id: str
    payload_hash: str
    fetched_at: datetime
    versions: int
    #: A handful of recognisable fields, so a list is readable at a glance.
    preview: dict[str, Any]
    size_bytes: int


class RecordPage(BaseModel):
    records: list[RecordSummary]
    total: int
    limit: int
    offset: int
    has_more: bool


class RecordDetail(BaseModel):
    source: str
    entity: str
    source_id: str
    payload_hash: str
    fetched_at: datetime
    sync_run_id: str | None
    versions: int
    payload: dict[str, Any]


class VersionSummary(BaseModel):
    payload_hash: str
    fetched_at: datetime
    sync_run_id: str | None
    size_bytes: int
    is_current: bool


class RecordHistory(BaseModel):
    source: str
    entity: str
    source_id: str
    versions: list[VersionSummary]


def _preview(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: payload[key] for key in PREVIEW_KEYS if key in payload}


def _size(payload: dict[str, Any]) -> int:
    from lnd.ingest.hashing import canonical_json

    return len(canonical_json(payload).encode("utf-8"))


# ---------------------------------------------------------------------------
@router.get("/summary", response_model=RawSummary)
def summary(user: CurrentUser, db: DbSession) -> RawSummary:
    """What is in the raw layer, by source and entity."""
    counts = db.execute(
        select(
            RawRecord.source,
            RawRecord.entity,
            func.count(func.distinct(RawRecord.source_id)).label("records"),
            func.count().label("versions"),
            func.min(RawRecord.fetched_at).label("first_seen"),
            func.max(RawRecord.fetched_at).label("last_seen"),
        ).group_by(RawRecord.source, RawRecord.entity)
    ).all()

    entities = []
    total = 0
    for row in sorted(counts, key=lambda r: (r.source, r.entity)):
        total += row.versions
        entities.append(
            EntityCount(
                source=row.source,
                entity=row.entity,
                records=row.records,
                versions=row.versions,
                first_seen=row.first_seen,
                last_seen=row.last_seen,
            )
        )

    return RawSummary(total_versions=total, entities=entities)


@router.get("/{source}/{entity}", response_model=RecordPage)
def list_records(
    source: Source,
    entity: Entity,
    user: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(description="Case-insensitive match on the payload")] = None,
) -> RecordPage:
    """The current version of each record, newest first.

    Metadata and a short preview only. `q` is a plain substring search over the
    stored JSON — crude, but it is exactly what you want when someone asks
    "does the CRM know about X?".
    """
    records = current(db, source=source, entity=entity)

    if q:
        needle = q.lower()
        from lnd.ingest.hashing import canonical_json

        records = [r for r in records if needle in canonical_json(r.payload).lower()]

    records.sort(key=lambda r: r.fetched_at, reverse=True)
    total = len(records)
    page = records[offset : offset + limit]

    version_counts: dict[str, int] = {
        row.source_id: row.versions
        for row in db.execute(
            select(RawRecord.source_id, func.count().label("versions"))
            .where(RawRecord.source == str(source), RawRecord.entity == str(entity))
            .group_by(RawRecord.source_id)
        ).all()
    }

    return RecordPage(
        records=[
            RecordSummary(
                source=r.source,
                entity=r.entity,
                source_id=r.source_id,
                payload_hash=r.payload_hash,
                fetched_at=r.fetched_at,
                versions=version_counts.get(r.source_id, 1),
                preview=_preview(r.payload),
                size_bytes=_size(r.payload),
            )
            for r in page
        ],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + limit < total,
    )


@router.get("/{source}/{entity}/{source_id}", response_model=RecordDetail)
def get_record(
    source: Source, entity: Entity, source_id: str, user: CurrentUser, db: DbSession
) -> RecordDetail:
    """The full stored payload, exactly as the source sent it."""
    records = current(db, source=source, entity=entity, source_ids=[source_id])
    if not records:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {source}/{entity} record with id {source_id} has ever landed.",
        )
    record = records[0]

    return RecordDetail(
        source=record.source,
        entity=record.entity,
        source_id=record.source_id,
        payload_hash=record.payload_hash,
        fetched_at=record.fetched_at,
        sync_run_id=record.sync_run_id,
        versions=len(history(db, source=source, entity=entity, source_id=source_id)),
        payload=record.payload,
    )


@router.get("/{source}/{entity}/{source_id}/history", response_model=RecordHistory)
def get_history(
    source: Source, entity: Entity, source_id: str, user: CurrentUser, db: DbSession
) -> RecordHistory:
    """Every version this record has had, oldest first.

    The append-only layer's real payoff: when a figure moves between cycles,
    this shows whether the source changed its mind and exactly when.
    """
    versions = history(db, source=source, entity=entity, source_id=source_id)
    if not versions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {source}/{entity} record with id {source_id} has ever landed.",
        )

    latest = versions[-1].id
    return RecordHistory(
        source=str(source),
        entity=str(entity),
        source_id=source_id,
        versions=[
            VersionSummary(
                payload_hash=v.payload_hash,
                fetched_at=v.fetched_at,
                sync_run_id=v.sync_run_id,
                size_bytes=_size(v.payload),
                is_current=v.id == latest,
            )
            for v in versions
        ],
    )
