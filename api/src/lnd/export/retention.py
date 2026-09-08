"""Keeping the reports that were published, so they can be produced later.

Every other export in this package is generated on demand and forgotten. The
monthly report is not, and the difference is not convenience — it is that
regenerating it does not reproduce it.

A report regenerated in October over August's window is the *current* answer for
August. Enrichment decisions, corrections, a trainer alias merged, CRM rows
arriving late: all of them move it, and usually toward the truth. That newer
answer is the better one for deciding anything. It is not the file that went out
in September, which is what somebody holding the mail is asking about — and once
the moment passes, only one of those two can still be produced.

So the bytes are kept, and the two questions stay separately answerable. That is
the same rule the raw layer follows for the CRM, and the same rule the
enrichment overlay follows for L&D's decisions.

WHAT IS NOT KEPT

Ad-hoc downloads. A drill-through of named attendees is generated for one
question by one person; retaining every one of them would accumulate a second
copy of the roster in a table nobody audits, and would answer no question the
listing is for. Retention here means the published artefact, not the browsing.

HOW MUCH IS KEPT

Every period, forever — twelve small files a year, and the record of what was
published is the point. What is capped is *re-generations of one period*: three,
newest first.

Before the cap there is a cheaper rule: a regeneration whose figures are
identical to the newest edition of that period is not stored at all. Nothing was
published that was not already published, and a list where every row is a real
difference is the only kind worth reading. Note that the test is the *figures*,
not the bytes — every export carries its own generation timestamp, so two
renderings of an unchanged month never match byte for byte and a digest of the
file would call them different every time.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.orm import Session, load_only

from lnd.metrics.base import MetricValue
from lnd.models.ops import ExportEdition, ExportKind, ExportTrigger

#: Re-generations of one period to keep. Three shows a restatement and its
#: predecessor without letting a loop fill the table.
RETAIN_PER_PERIOD = 3


def figures_digest(values: Iterable[MetricValue]) -> str:
    """A digest of what the figures were, independent of how they were rendered.

    Over `key=value` for every metric, sorted, so it does not depend on the
    order the registry happened to return them in. `None` is written as the
    word rather than skipped: a metric that could not be measured and a metric
    that was measured are different states, and collapsing them would let a
    month go from "no value" to a number without the digest noticing.
    """
    lines = sorted(
        f"{value.key}={'none' if value.value is None else value.value}" for value in values
    )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def keep(
    session: Session,
    *,
    kind: ExportKind,
    trigger: ExportTrigger,
    year: int,
    month: int,
    filename: str,
    content_type: str,
    filters_applied: str,
    content: bytes,
    figures_sha256: str,
    generated_by: str | None,
) -> ExportEdition:
    """Store one generated report as an edition, unless it says nothing new.

    A regeneration whose figures match the newest edition of that period is not
    stored: the existing row is returned instead. Nothing was published that was
    not already published, and the alternative is a list of identical rows in
    which the one edition that did change is hard to see.

    `generated_by` is None for a scheduled run on purpose. Writing a service
    account into an author column would make the trail say a person did
    something nobody did, which is worse than saying nobody did.
    """
    newest = latest_for(session, kind=kind, year=year, month=month)
    if newest is not None and newest.figures_sha256 == figures_sha256:
        return newest

    edition = ExportEdition(
        kind=kind,
        trigger=trigger,
        period_year=year,
        period_month=month,
        filename=filename,
        content_type=content_type,
        filters_applied=filters_applied,
        generated_by=generated_by,
        byte_size=len(content),
        figures_sha256=figures_sha256,
        content=content,
    )
    session.add(edition)
    session.flush()
    prune(session, kind=kind, year=year, month=month)
    return edition


def prune(session: Session, *, kind: ExportKind, year: int, month: int) -> int:
    """Drop all but the newest `RETAIN_PER_PERIOD` editions of one period.

    Scoped to one period rather than run across the table, because "keep the
    last N reports" would quietly delete January the moment a year had passed.
    Every period is kept; only repeats within one are capped.
    """
    keep_ids = session.scalars(
        select(ExportEdition.id)
        .where(
            ExportEdition.kind == kind,
            ExportEdition.period_year == year,
            ExportEdition.period_month == month,
        )
        .order_by(ExportEdition.generated_at.desc(), ExportEdition.id.desc())
        .limit(RETAIN_PER_PERIOD)
    ).all()
    if not keep_ids:
        return 0
    # `session.execute` is typed as returning `Result`; a DELETE returns a
    # `CursorResult`, which is the only one of the two carrying `rowcount`.
    statement = delete(ExportEdition).where(
        ExportEdition.kind == kind,
        ExportEdition.period_year == year,
        ExportEdition.period_month == month,
        ExportEdition.id.notin_(keep_ids),
    )
    result = cast("CursorResult[Any]", session.execute(statement))
    return result.rowcount or 0


def editions(
    session: Session, *, kind: ExportKind | None = None, limit: int = 100
) -> Sequence[ExportEdition]:
    """The listing, newest first, *without* the files.

    `load_only` is not an optimisation here. The listing is one row per report
    and the content column is the report; loading it to render a filename would
    pull every edition in the table into memory to answer a question about none
    of them.
    """
    statement = (
        select(ExportEdition)
        .options(
            load_only(
                ExportEdition.id,
                ExportEdition.kind,
                ExportEdition.trigger,
                ExportEdition.period_year,
                ExportEdition.period_month,
                ExportEdition.filename,
                ExportEdition.content_type,
                ExportEdition.filters_applied,
                ExportEdition.generated_at,
                ExportEdition.generated_by,
                ExportEdition.byte_size,
                ExportEdition.figures_sha256,
            )
        )
        .order_by(ExportEdition.generated_at.desc(), ExportEdition.id.desc())
        .limit(limit)
    )
    if kind is not None:
        statement = statement.where(ExportEdition.kind == kind)
    return session.scalars(statement).all()


def fetch(session: Session, edition_id: int) -> ExportEdition | None:
    """One edition, with its bytes. The only place `content` is read."""
    return session.get(ExportEdition, edition_id)


def latest_for(
    session: Session, *, kind: ExportKind, year: int, month: int
) -> ExportEdition | None:
    """The most recent edition of one period, if any was ever kept."""
    return session.scalars(
        select(ExportEdition)
        .where(
            ExportEdition.kind == kind,
            ExportEdition.period_year == year,
            ExportEdition.period_month == month,
        )
        .order_by(ExportEdition.generated_at.desc(), ExportEdition.id.desc())
        .limit(1)
    ).first()


def stored_bytes(session: Session) -> tuple[int, int]:
    """How many editions are held and how much they weigh.

    Shown in the console. A table that grows without anybody watching is how a
    retention policy becomes a disk-space incident, and the honest answer here
    is small — which is worth being able to demonstrate rather than assert.
    """
    row = session.execute(
        select(func.count(ExportEdition.id), func.coalesce(func.sum(ExportEdition.byte_size), 0))
    ).one()
    return int(row[0]), int(row[1])


__all__ = [
    "RETAIN_PER_PERIOD",
    "editions",
    "fetch",
    "figures_digest",
    "keep",
    "latest_for",
    "prune",
    "stored_bytes",
]
