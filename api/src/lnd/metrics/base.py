"""What a metric is.

Two shapes and no more. A `ScalarMetric` is a count or a sum; a `RatioMetric`
carries its numerator and denominator separately and divides at the last moment.

**Why a ratio may not be stored as a number.** Because it cannot be aggregated
afterwards. Two programs scoring 100% over 2 responses and 80% over 98 do not
combine to 90%; they combine to 80.4%. Averaging ratios is right only while
every group has the same size, which is true of this dataset today and will stop
being true the moment anybody breaks a score down by department. Keeping both
terms means the breakdown adds up whatever the group sizes are.

That is *not* the defence against P-03, and it is worth being exact about which
problem each mechanism solves. P-03 was a scope error, not an averaging one —
every row in the workbook already scored +1, 0 or -1, so the mean equalled the
formula. The number was wrong because it ran over a silently filtered 55 of 77
responses. What prevents that is `Population`, the explicit filter set, and the
response count returned beside every score. `RatioMetric` prevents the different
bug that has not happened yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from sqlalchemy.orm import Session

from lnd.metrics.filters import Dimension, MetricFilters, UnsupportedFilter
from lnd.metrics.population import Population


class Provenance(StrEnum):
    """How this metric relates to the figure the workbook published.

    Carried into the reconciliation statement, so "why has this changed?" is
    answered by the metric itself rather than by somebody's memory.
    """

    #: Same definition, same number.
    UNCHANGED = "unchanged"
    #: Same intent, different and better-defined computation.
    RESTATED = "restated"
    #: Same computation, a name that says what it means.
    RENAMED = "renamed"
    #: The published figure was wrong. The correction is the point.
    CORRECTED = "corrected"
    #: The workbook did not have this at all.
    NEW = "new"


class Unit(StrEnum):
    COUNT = "count"
    HOURS = "hours"
    PERCENT = "percent"
    #: -100 to +100. Not a percentage, and shown with a sign so nobody reads
    #: +88 as a fall from the workbook's "92.7%".
    NPS = "nps"
    MONTHS = "months"


@dataclass(frozen=True)
class MetricValue:
    """A number and everything needed to read it honestly.

    The definition, the population and the filters travel with the value rather
    than beside it. A figure that arrives without its scope is a figure somebody
    will put in a slide without its scope.
    """

    key: str
    title: str
    definition: str
    population: Population
    provenance: Provenance
    unit: Unit

    value: Decimal | None
    #: Present on ratios. `None` on scalars, where there is no denominator to
    #: show and pretending otherwise would invite a meaningless "of what?".
    numerator: Decimal | None = None
    denominator: Decimal | None = None

    #: How many rows the value rests on. Returned for every metric, not only
    #: ratios: a quality score over 297 responses and one over 4 are different
    #: claims, and only this distinguishes them on screen.
    sample_size: int = 0

    filters_applied: str = "no filters — the full population"
    dimensions_filtered: frozenset[Dimension] = field(default_factory=frozenset)

    #: True where the answer rests on estimated dimension rows — a headcount
    #: asked for a date before the platform began snapshotting, say.
    is_estimated: bool = False

    @property
    def is_defined(self) -> bool:
        """False where the denominator was zero.

        Distinct from a value of zero, and the distinction matters: "no
        enrollments, so no no-show rate" is not "a no-show rate of 0%".
        """
        return self.value is not None

    def formatted(self) -> str:
        if self.value is None:
            return "—"
        if self.unit is Unit.PERCENT:
            return f"{self.value:.1f}%"
        if self.unit is Unit.NPS:
            return f"{self.value:+.1f}"
        if self.unit is Unit.HOURS:
            return f"{self.value:,.1f} hours"
        if self.unit is Unit.MONTHS:
            return f"{self.value:.1f} months"
        return f"{self.value:,.0f}"


@dataclass(frozen=True)
class Ratio:
    """A numerator over a denominator, kept apart until the last moment."""

    numerator: Decimal
    denominator: Decimal

    @property
    def value(self) -> Decimal | None:
        if self.denominator == 0:
            return None
        return self.numerator / self.denominator

    def __add__(self, other: Ratio) -> Ratio:
        """Combining two groups adds both terms, never averages two ratios.

        This is the whole reason the type exists. `(2/2) + (78/98)` is `80/100`,
        which is 80.4% — not the 90% that averaging 100% and 80% would give.
        """
        return Ratio(
            numerator=self.numerator + other.numerator,
            denominator=self.denominator + other.denominator,
        )


@dataclass(frozen=True)
class MetricSpec:
    """Everything about a metric except how to fetch its numbers.

    Split out so the declaration reads as a catalogue entry — which is what the
    twenty-one of them are — and so the definition string that ships to the UI
    and stamps every export sits next to the population it applies to.
    """

    key: str
    title: str
    definition: str
    population: Population
    provenance: Provenance
    unit: Unit
    supports: frozenset[Dimension]
    #: Why it changed, for the reconciliation statement. Empty for new metrics
    #: and for the three that did not move.
    note: str = ""

    def reject_unsupported(self, filters: MetricFilters) -> None:
        unsupported = filters.dimensions_used - self.supports
        if unsupported:
            names = ", ".join(sorted(d.value for d in unsupported))
            raise UnsupportedFilter(
                f"{self.key} does not define a population narrowed by {names}. "
                f"It counts over {self.population.description}. Answering anyway "
                f"would return a number filtered on one side only."
            )


class Metric(Protocol):
    """The contract every metric satisfies.

    A metric is its spec plus a way to measure. Keeping the spec whole rather
    than spreading its fields across the protocol is what lets the registry, the
    exports and the reconciliation statement pass one object around instead of
    seven parallel arguments that could get out of step.
    """

    #: A read-only property rather than a plain attribute: every metric is a
    #: frozen dataclass, and a mutable protocol attribute would refuse them all.
    @property
    def spec(self) -> MetricSpec: ...

    def compute(self, session: Session, filters: MetricFilters) -> MetricValue: ...
