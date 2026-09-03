"""The raw landing table.

One table for every source and every entity. The shape of a CRM program and the
shape of an HRIS employee have nothing in common, and inventing a table per
entity here would mean a migration every time a source adds a field — which is
exactly the coupling the raw layer exists to avoid. The payload is stored as
received, and only the transform in week 3 has an opinion about its contents.

Append-only. A changed record is a new row, not an edit, so the full history of
what each source said and when is recoverable. The database enforces this: the
application role has SELECT and INSERT on `raw` and no UPDATE or DELETE
(migration 0001).
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from lnd.db import SCHEMA_RAW, Base


class Source(enum.StrEnum):
    """Where a record came from.

    Three sources, not the four BRD v1.5 assumed. Feedback turned out to live
    in the CRM after all — the Learning Program Dataset API returns `survey`,
    `survey_answers[]` and `assessment_answers[]` nested inside each program —
    so Microsoft Forms via the Graph API is not a source, and Q-14 is closed.
    """

    CRM = "crm"
    HRIS = "hris"
    LINKEDIN = "linkedin"


class Entity(enum.StrEnum):
    """What kind of thing a record is, within its source."""

    PROGRAM = "program"
    SESSION = "session"
    ENROLLMENT = "enrollment"
    ATTENDANCE = "attendance"
    EVALUATION = "evaluation"
    EMPLOYEE = "employee"
    COURSE_COMPLETION = "course_completion"  # LinkedIn Learning


class RawRecord(Base):
    __tablename__ = "source_record"
    __table_args__ = (
        # Dedup. Re-fetching a record whose content has not changed collides
        # here and is skipped; a record whose content HAS changed has a
        # different hash, so it lands as a new row and history survives.
        UniqueConstraint(
            "source", "entity", "source_id", "payload_hash", name="uq_raw_record_content"
        ),
        # "Give me the current version of every program" — the query the
        # transform runs on every pass.
        Index(
            "ix_raw_record_current",
            "source",
            "entity",
            "source_id",
            "fetched_at",
        ),
        # "What arrived in this sync run?" — for the week-2 audit trail.
        Index("ix_raw_record_sync_run", "sync_run_id"),
        {"schema": SCHEMA_RAW},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    source: Mapped[str] = mapped_column(String(16), nullable=False)
    entity: Mapped[str] = mapped_column(String(32), nullable=False)

    #: The natural key exactly as the source spells it. Kept as text because a
    #: CRM integer id, a Graph GUID and an email address all have to fit.
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)

    #: Exactly what arrived. Never cleaned, never normalised, never corrected.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    payload_hash: Mapped[str] = mapped_column(String(80), nullable=False)

    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    #: Which sync produced this row. The ops.sync_run table arrives in week 2;
    #: until then this is populated but unconstrained.
    sync_run_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)

    def __repr__(self) -> str:
        return (
            f"RawRecord({self.source}/{self.entity}/{self.source_id} "
            f"@{self.fetched_at:%Y-%m-%d %H:%M} {self.payload_hash[:16]}…)"
        )
