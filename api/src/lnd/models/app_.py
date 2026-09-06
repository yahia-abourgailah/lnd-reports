"""Enrichment and the quarantine queue.

`app` holds what people add on top of what the sources say. The enrichment
overrides arrive in week 6; the quarantine queue arrives now, because the
transform cannot be written honestly without it.

**Why a queue rather than a null.** The transform meets rows it cannot key —
an attendance whose `employee_code` is absent, a sector nobody has seen before,
a session whose times will not subtract. Every one of those has a tempting
silent answer: null the column, coalesce to 'Unknown', drop the row. All three
produce a report that is quietly short and looks fine, which is precisely the
failure the workbook had. So a row the transform cannot key does not enter
`core` at all — it lands here, with the reason and the payload that produced it,
and it is counted.

That counting is the point. `received = counted + quarantined` is asserted
before every commit, so a row can be rejected but not lost: the two numbers are
either equal or the run fails with the dashboard still serving the last good
state.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lnd.db import SCHEMA_APP, Base
from lnd.ingest.models import Entity, Source
from lnd.models.ops import _enum_column


class QuarantineReason(StrEnum):
    """Why a row could not enter `core`.

    Coarse on purpose. The free-text `detail` carries the specifics; this is
    what the data-quality queue groups by, and a hundred distinct reasons would
    make it unreadable. Each value maps to one action somebody can actually
    take.
    """

    #: The row references a person the roster does not contain. The action is a
    #: question to the CRM team, not a transform change.
    UNKNOWN_EMPLOYEE = "unknown_employee"
    #: A parent the row hangs off — a program, a session — is not in `core`.
    UNKNOWN_PARENT = "unknown_parent"
    #: A categorical arrived with a value no mapping covers. The action is to
    #: extend the mapping, which is why the raw value is kept verbatim.
    UNMAPPED_CATEGORY = "unmapped_category"
    #: A required field is absent or empty in the payload.
    MISSING_REQUIRED = "missing_required"
    #: Present but unusable — a grade that will not parse, times that will not
    #: subtract, a rating outside its scale.
    INVALID_VALUE = "invalid_value"
    #: The row collides with one already counted on the grain's unique key.
    #: Duplicate badge scans land here rather than aborting the run.
    DUPLICATE = "duplicate"


class Quarantine(Base):
    """One row the transform refused, with enough context to act on it."""

    __tablename__ = "quarantine"
    __table_args__ = (
        # The queue is read two ways and only two: "what is outstanding, worst
        # first" and "everything wrong with this entity". Both start here.
        Index(
            "ix_quarantine_open",
            "source",
            "entity",
            "reason",
            postgresql_where=text("NOT is_resolved"),
        ),
        {"schema": SCHEMA_APP},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    source: Mapped[Source] = mapped_column(
        _enum_column(Source, "quarantine_source"), nullable=False
    )
    entity: Mapped[Entity] = mapped_column(
        _enum_column(Entity, "quarantine_entity"), nullable=False
    )
    reason: Mapped[QuarantineReason] = mapped_column(
        _enum_column(QuarantineReason, "quarantine_reason"), nullable=False
    )

    #: The source's own id for the row, where it has one. Null when the row is
    #: nested and unkeyed — a survey answer inside a program, say.
    source_id: Mapped[str | None] = mapped_column(Text)
    #: What was wrong, in a sentence a person can act on. "sector 'Fnance' is
    #: not in the mapping" beats "validation failed".
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    #: The offending fragment, verbatim. Not the whole program payload — the
    #: attendance row, the answer, the user object — so the queue stays small
    #: and the reader sees the thing itself rather than 90 kB around it.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    #: The run that rejected it, so the queue joins to the audit trail.
    sync_run_id: Mapped[int | None] = mapped_column(BigInteger)

    first_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: Resolved by a fix at source, or by an enrichment override. Kept rather
    #: than deleted: "this was wrong for three weeks and then fixed" is the
    #: history that tells you whether data quality is improving.
    is_resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
