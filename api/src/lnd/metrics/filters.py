"""The one filter set every metric and endpoint accepts.

One shape, applied identically everywhere, and — this is the point — applied
only where a metric says it applies. A filter is passed, never picked up.

Each metric declares which of these it honours. Asking for a trainer breakdown
of Participation Rate is not silently answered with an unfiltered number; it is
refused, because a rate whose numerator was narrowed by trainer and whose
denominator was not is exactly the arithmetic P-01 and P-13 produced.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import StrEnum


class Dimension(StrEnum):
    """A filter, and a thing you can break a metric down by.

    The same vocabulary for both on purpose. "Filter to Finance" and "break down
    by sector" are one concept applied twice, and giving them separate names is
    how two spellings of one dimension come to disagree.
    """

    PERIOD = "period"
    SECTOR = "sector"
    DEPARTMENT = "department"
    COMPANY = "company"
    JOB_LEVEL = "job_level"
    PROGRAM = "program"
    PROGRAM_TYPE = "program_type"
    PROGRAM_TARGET = "program_target"
    TRAINER = "trainer"


@dataclass(frozen=True)
class MetricFilters:
    """What was asked for. Empty means everything, and says so.

    Frozen so a filter cannot be added downstream of the metric that validated
    it — the mechanism by which P-03's hidden filter would come back.
    """

    date_from: dt.date | None = None
    date_to: dt.date | None = None
    sectors: frozenset[str] = field(default_factory=frozenset)
    departments: frozenset[str] = field(default_factory=frozenset)
    companies: frozenset[str] = field(default_factory=frozenset)
    job_levels: frozenset[str] = field(default_factory=frozenset)
    program_ids: frozenset[int] = field(default_factory=frozenset)
    program_types: frozenset[str] = field(default_factory=frozenset)
    program_targets: frozenset[str] = field(default_factory=frozenset)
    trainer_keys: frozenset[int] = field(default_factory=frozenset)

    @property
    def dimensions_used(self) -> frozenset[Dimension]:
        """Which dimensions were actually narrowed.

        Returned with every result. A number that was filtered and a number that
        was not must never look alike on screen, which is the failure P-03 was.
        """
        used: set[Dimension] = set()
        if self.date_from is not None or self.date_to is not None:
            used.add(Dimension.PERIOD)
        for name, dimension in (
            ("sectors", Dimension.SECTOR),
            ("departments", Dimension.DEPARTMENT),
            ("companies", Dimension.COMPANY),
            ("job_levels", Dimension.JOB_LEVEL),
            ("program_ids", Dimension.PROGRAM),
            ("program_types", Dimension.PROGRAM_TYPE),
            ("program_targets", Dimension.PROGRAM_TARGET),
            ("trainer_keys", Dimension.TRAINER),
        ):
            if getattr(self, name):
                used.add(dimension)
        return frozenset(used)

    @property
    def is_empty(self) -> bool:
        return not self.dimensions_used

    def describe(self) -> str:
        """A sentence for the export footer and the tooltip.

        The number, its definition and its scope ship together or not at all.
        """
        if self.is_empty:
            return "no filters — the full population"

        parts: list[str] = []
        if self.date_from or self.date_to:
            start = self.date_from.isoformat() if self.date_from else "the beginning"
            end = self.date_to.isoformat() if self.date_to else "today"
            parts.append(f"{start} to {end}")
        for name, label in (
            ("sectors", "sector"),
            ("departments", "department"),
            ("companies", "company"),
            ("job_levels", "job level"),
            ("program_types", "type"),
            ("program_targets", "target"),
        ):
            values = getattr(self, name)
            if values:
                parts.append(f"{label} in {', '.join(sorted(values))}")
        if self.program_ids:
            parts.append(f"{len(self.program_ids)} program(s)")
        if self.trainer_keys:
            parts.append(f"{len(self.trainer_keys)} trainer(s)")
        return "; ".join(parts)

    def without(self, *dimensions: Dimension) -> MetricFilters:
        """The same filters with some cleared.

        Used where a metric's denominator is legitimately wider than its
        numerator — Participation Rate counts attendance in the period against
        the headcount at period end, not against the headcount of people who
        attended. Clearing is explicit and returns a new object, so the original
        filters cannot be mutated out from under the caller.
        """
        wanted = set(dimensions)
        empty: frozenset[str] = frozenset()
        empty_ids: frozenset[int] = frozenset()
        keep_period = Dimension.PERIOD not in wanted

        def held(dimension: Dimension) -> bool:
            return dimension not in wanted

        return MetricFilters(
            date_from=self.date_from if keep_period else None,
            date_to=self.date_to if keep_period else None,
            sectors=self.sectors if held(Dimension.SECTOR) else empty,
            departments=self.departments if held(Dimension.DEPARTMENT) else empty,
            companies=self.companies if held(Dimension.COMPANY) else empty,
            job_levels=self.job_levels if held(Dimension.JOB_LEVEL) else empty,
            program_ids=self.program_ids if held(Dimension.PROGRAM) else empty_ids,
            program_types=self.program_types if held(Dimension.PROGRAM_TYPE) else empty,
            program_targets=self.program_targets if held(Dimension.PROGRAM_TARGET) else empty,
            trainer_keys=self.trainer_keys if held(Dimension.TRAINER) else empty_ids,
        )


class UnsupportedFilter(ValueError):
    """A metric was asked to honour a dimension it does not define.

    Deliberately an error rather than a shrug. Ignoring the filter would answer
    the question that was not asked, with a number that looks like the one that
    was — which is the whole of P-03.
    """
