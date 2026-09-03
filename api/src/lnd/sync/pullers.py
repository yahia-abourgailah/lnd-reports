"""The contract between the sync runner and a source client.

The runner does the same six things for every entity — check the breaker, read
the watermark, fetch with backoff, land, count, advance — and none of them
depend on which source is being read. What differs is only: which pairs of
`(source_id, payload)` come back, which errors are worth repeating, and whether
the source can be asked for a window at all.

That is what a puller is. It keeps `CrmClient` unaware of watermarks and the
runner unaware of HTTP, so a second source is a new puller rather than a second
runner.

**Whether the source can filter by modification date is a property of the
source, not of the sync.** The CRM's documented contract is `filter[id]`,
`filter[type]` and so on; nothing says a date filter exists, and an unsupported
filter is a 400 rather than a silently ignored parameter. So the puller takes
the filter's name as configuration: given one, it narrows the request; given
nothing, it asks for everything and lets the landing layer discard what has not
changed. Both are correct — the second is merely wasteful — and switching
between them is one setting, not a rewrite.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from lnd.config import get_settings
from lnd.ingest.models import Entity, Source
from lnd.sources.crm.client import CrmClient, CrmError

log = logging.getLogger(__name__)

#: What `land()` consumes: the natural key exactly as the source spells it,
#: paired with the payload exactly as it arrived.
Record = tuple[str, dict[str, Any]]


@runtime_checkable
class SourcePuller(Protocol):
    """One entity, from one source."""

    #: Which pair this puller is responsible for. The runner reads these rather
    #: than being told, so a caller cannot ask the programs puller to record
    #: itself as an attendance sync.
    source: Source
    entity: Entity

    #: Exception types worth a second attempt. Paired with
    #: `honours_retryable_flag`, so a client raising one class for every
    #: failure can still refuse a retry per instance.
    transient_errors: tuple[type[Exception], ...]

    def fetch(self, *, changed_since: datetime | None) -> Iterable[Record]:
        """Records from the source, newest state first if the source has an order.

        `changed_since` is a request, not a guarantee. A puller whose source
        cannot filter by date ignores it and returns everything; the landing
        layer's content hash is what stops that costing anything downstream.
        """
        ...


class MissingNaturalKey(RuntimeError):
    """A payload arrived without the field that identifies it.

    Landing it would be worse than failing: a record with no stable key cannot
    be deduplicated, cannot be updated, and cannot be found again.
    """


@dataclass
class CrmProgramPuller:
    """Programs from the CRM's Learning Program Dataset.

    Evaluations arrive nested inside each program — `survey`, `survey_answers[]`
    and `assessment_answers[]` — so this one endpoint carries what was once
    assumed to need Microsoft Forms (Q-03). Splitting them into their own
    entities is the transform's job in week 3; the raw layer stores the program
    exactly as it came.
    """

    client: CrmClient
    changed_since_filter: str | None = None

    source: Source = field(default=Source.CRM, init=False)
    entity: Entity = field(default=Entity.PROGRAM, init=False)
    transient_errors: tuple[type[Exception], ...] = field(default=(CrmError,), init=False)

    @classmethod
    def from_settings(cls, client: CrmClient) -> CrmProgramPuller:
        configured = get_settings().crm_changed_since_filter
        return cls(client=client, changed_since_filter=configured or None)

    def fetch(self, *, changed_since: datetime | None) -> Iterator[Record]:
        filters: dict[str, str] = {}

        if changed_since is not None and self.changed_since_filter:
            filters[self.changed_since_filter] = changed_since.isoformat()
        elif changed_since is not None:
            log.info(
                "source cannot narrow by date; fetching everything",
                extra={
                    "event": "sync.fetch.unfiltered",
                    "source": str(self.source),
                    "entity": str(self.entity),
                    "changed_since": changed_since,
                },
            )

        for payload in self.client.iter_programs(**filters):
            identifier = payload.get("id")
            if identifier is None:
                raise MissingNaturalKey(
                    f"a {self.source} {self.entity} payload arrived without an `id`"
                )
            yield str(identifier), payload
