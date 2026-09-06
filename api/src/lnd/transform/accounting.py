"""Every row in, accounted for.

`received = counted + quarantined`, checked before the transaction commits.

The rule exists because the alternative is invisible. A transform that drops a
row it cannot key produces a report that is simply short — no error, no log
line, nothing on screen to distinguish "42 attendances" from "42 of the 47 we
were given". The workbook failed exactly this way, and nobody could have known
by looking at it.

So a row leaves this pipeline through one of two doors and there is no third.
Either it is counted into `core`, or it is written to `app.quarantine` with a
reason. If those two do not add up to what came in, the run raises and the
transaction rolls back, and the dashboard keeps serving the last good state
rather than a quietly incomplete one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lnd.ingest.models import Entity, Source


class TransformInvariantError(RuntimeError):
    """Rows went missing between `raw` and `core`.

    Never a data problem — malformed data is quarantined, which is accounted
    for. This is always a bug in the transform: a branch that returns early, a
    filter applied before the tally, an exception swallowed in a loop. It fails
    the run on purpose rather than logging, because a partially applied
    transform is worse than no transform at all.
    """


@dataclass
class RunTally:
    """What one entity's transform did, counted as it goes.

    Incremented at the point each decision is made rather than summed
    afterwards, so the tally cannot drift from the work: there is no separate
    pass that could count a row the loop skipped.
    """

    source: Source
    entity: Entity
    received: int = 0
    counted: int = 0
    quarantined: int = 0
    #: Rows that were already present and identical. Counted, not new — an
    #: unchanged employee on the second run of the day is not a missing row.
    unchanged: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def count(self, n: int = 1) -> None:
        self.counted += n

    def keep_unchanged(self, n: int = 1) -> None:
        self.counted += n
        self.unchanged += n

    def quarantine(self, reason: str, n: int = 1) -> None:
        self.quarantined += n
        self.reasons[reason] = self.reasons.get(reason, 0) + n

    @property
    def balances(self) -> bool:
        return self.received == self.counted + self.quarantined

    def assert_balanced(self) -> None:
        """Call before commit. Raises rather than returns a flag.

        A boolean would be checked by the first caller and forgotten by the
        second; there is no safe way to ignore an exception.
        """
        if self.balances:
            return

        difference = self.received - self.counted - self.quarantined
        # Named rather than signed. This is read from a log line at 3am, and
        # "-1 unaccounted for" leaves the reader deriving which direction is
        # which before they can start.
        fault = (
            f"{difference} row(s) were dropped"
            if difference > 0
            else f"{-difference} row(s) were counted twice"
        )
        raise TransformInvariantError(
            f"{self.source}/{self.entity}: received {self.received} but accounted for "
            f"{self.counted + self.quarantined} "
            f"(counted {self.counted}, quarantined {self.quarantined}) — {fault}."
        )

    def as_log_fields(self) -> dict[str, int | str]:
        fields: dict[str, int | str] = {
            "source": self.source.value,
            "entity": self.entity.value,
            "received": self.received,
            "counted": self.counted,
            "unchanged": self.unchanged,
            "quarantined": self.quarantined,
        }
        for reason, n in sorted(self.reasons.items()):
            fields[f"quarantined_{reason}"] = n
        return fields
