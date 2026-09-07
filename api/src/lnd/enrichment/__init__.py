"""Human decisions layered over what the sources say.

Five tables in `app`, all shaped the same way: a natural key, a value somebody
chose, and who chose it when. The transform reads them; nothing here ever
touches `raw` or `core`.

WHY OVERRIDES ARE NOT EDITS

An override does not correct the CRM. The CRM keeps saying what it says, and
`raw` keeps the payload it said it in — that is what makes the whole pipeline
replayable. The override sits beside it and wins at one point in the transform,
so "what did the source say" and "what did we decide" remain separately
answerable forever. A pipeline that edited the source data to fix a trainer's
name could never answer the first question again.

WHY NOTHING IS UPDATED IN PLACE

A change supersedes the old row and inserts a new one. The audit trail (FR-C03,
NFR-07) is then a property of the storage rather than a second table somebody
has to remember to write to — and the failure mode of an audit log you must
remember to write is that the one change worth auditing is the one that skipped
it.

It also means "what was this before?" and "why was it changed?" survive the
person who changed it, which for a table whose whole content is judgement calls
is the point.
"""

from __future__ import annotations

from lnd.enrichment.service import (
    EnrichmentConflict,
    OverlayEntry,
    OverlayKind,
    author,
    history,
    live,
    retire,
)

__all__ = [
    "EnrichmentConflict",
    "OverlayEntry",
    "OverlayKind",
    "author",
    "history",
    "live",
    "retire",
]
