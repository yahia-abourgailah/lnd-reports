"""raw → core.

The transform is a pure function of `raw` plus `app` enrichment. It never calls
a source: everything it needs was landed by the sync, so a fix to a rule replays
over all of history without asking the CRM for anything, and the same input
always produces the same `core`.

Three rules hold everywhere in this package.

**Conform on the way in, never on the way out.** `sector` is trimmed here,
`job_level_grade` is cast to an integer here, `nps_band` is derived here. A
report that trimmed at query time would be one forgotten `trim()` away from
inventing five sectors, and there is no way to enforce a convention that lives
in every query.

**Read the current version only.** `raw.source_record` is append-only and
already holds several versions of every program. Every read starts with
`DISTINCT ON (source_id) ... ORDER BY source_id, fetched_at DESC, id DESC`, and
`lnd.ingest.landing.current` is the one implementation of it.

**Account for every row.** `received = counted + quarantined`, asserted before
commit. See `lnd.transform.accounting`.
"""

from __future__ import annotations

from lnd.transform.accounting import RunTally, TransformInvariantError
from lnd.transform.conform import (
    conform_grade,
    conform_optional_text,
    conform_sector,
)

__all__ = [
    "RunTally",
    "TransformInvariantError",
    "conform_grade",
    "conform_optional_text",
    "conform_sector",
]
