"""What every exported file has to say about itself.

The dashboard carries four things in its envelope — freshness, the filters
applied, how many rows are excluded, how many merely flagged — and a hover away
it carries each metric's definition and population. None of that follows a file
onto somebody's laptop.

That asymmetry is the whole reason this module exists. A figure on screen can be
re-checked: it is next to its definition, and the badge above it says how old it
is. A spreadsheet emailed in March and quoted in June has neither, and by then
the screen it came from shows different numbers for good reasons nobody in the
room remembers. The only thing standing between that and an argument is a line
of text on the sheet saying what the figure counted and when.

So the stamp is not a footer. It is the part of the export that makes the rest
of it readable six months later, and it carries the definition strings as well
as the numbers — a definition is worth more in an attachment, where it cannot be
looked up, than on a screen where it was one hover away.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from lnd.metrics import aggregate
from lnd.metrics.base import MetricValue
from lnd.metrics.filters import MetricFilters
from lnd.sync.freshness import platform_freshness

#: The sentence that must travel with any figure a reader might set beside the
#: workbook's. Never the bare numbers: 60.4% and 9.3% side by side without this
#: reads as a collapse in training, which is not what happened.
RECONCILIATION_NOTE = (
    "Figures restated against the workbook are corrections, not declines. "
    "Participation was published as 60.4% by dividing five companies' attendance "
    "by one company's headcount, after dividing by a hardcoded 192; like for like "
    "it is 9.3%. NPS is an index on -100..+100 and is not comparable with the "
    "workbook's 92.7%, which was a percentage. See docs/reconciliation.md."
)


@dataclass(frozen=True)
class Stamp:
    """Everything an export says about its own scope and age."""

    generated_at: datetime
    filters_applied: str
    freshness_status: str
    freshness_detail: str
    excluded_count: int
    flagged_count: int
    #: `(title, definition, population)` for each figure in the file. Carried
    #: rather than referenced: a link to a definition is a link somebody would
    #: have to be able to reach.
    definitions: tuple[tuple[str, str, str], ...] = ()

    def as_rows(self) -> tuple[tuple[str, str], ...]:
        """The stamp as label/value pairs, in reading order.

        One representation, rendered by both writers. A CSV stamp and an XLSX
        stamp that were built separately would be two answers to "how fresh is
        this?", and the one in the wrong file would be the one somebody quotes.
        """
        rows = [
            ("Generated at", self.generated_at.isoformat(timespec="seconds")),
            ("Filters applied", self.filters_applied),
            ("Data freshness", f"{self.freshness_status} — {self.freshness_detail}"),
            (
                "Records excluded",
                f"{self.excluded_count} excluded from the figures a rule affects",
            ),
            ("Records flagged", f"{self.flagged_count} flagged for review and counted in full"),
            ("Source", "L&D Analytics Platform, computed from the metric registry"),
            ("Note", RECONCILIATION_NOTE),
        ]
        rows.extend(
            (f"Definition — {title}", f"{definition} Counted over {population}.")
            for title, definition, population in self.definitions
        )
        return tuple(rows)


def _freshness_detail(session: Session) -> tuple[str, str]:
    """The platform's age, as two strings a spreadsheet can hold.

    There is no platform-level lag — one source can be current while another has
    never run — so the detail names the worst entity rather than inventing a
    single number the API does not return.
    """
    freshness = platform_freshness(session)
    worst = ""
    lag: float | None = None
    for source in freshness.sources:
        for entity in source.entities:
            if entity.lag_seconds is None:
                if entity.status == "never_synced":
                    worst, lag = f"{source.source}/{entity.entity} has never synced", None
                continue
            if lag is None or entity.lag_seconds > lag:
                worst = (
                    f"{source.source}/{entity.entity} last succeeded "
                    f"{int(entity.lag_seconds // 60)} minutes ago"
                )
                lag = entity.lag_seconds
    return freshness.status, worst or "no sync history"


def stamp(
    session: Session,
    filters: MetricFilters,
    values: tuple[MetricValue, ...] | list[MetricValue] = (),
) -> Stamp:
    """Build the stamp for one export.

    Reads the same `completeness` the dashboard envelope does, so a file and the
    screen it was taken from cannot disagree about how many rows were left out.
    """
    quality = aggregate.completeness(session)
    status, detail = _freshness_detail(session)
    return Stamp(
        generated_at=datetime.now(UTC),
        filters_applied=filters.describe(),
        freshness_status=status,
        freshness_detail=detail,
        excluded_count=quality.excluded,
        flagged_count=quality.flagged,
        definitions=tuple(
            (value.title, value.definition, value.population.description) for value in values
        ),
    )


__all__ = ["RECONCILIATION_NOTE", "Stamp", "stamp"]
