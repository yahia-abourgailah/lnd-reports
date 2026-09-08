"""What an export contains, before anything decides how to write it.

An `ExportTable` is columns, rows and a stamp. CSV and XLSX are two renderings
of the same object, which is why a figure cannot differ between the two formats
of one export — there is one table and two writers, not two builders.

THE ONE RULE

Every number here comes from `lnd.metrics.registry` or from
`lnd.metrics.drilldown`, which itself derives its rows from the metric's
declared population. Nothing in this package computes a figure.

That rule matters more in an export than it did on a scorecard, and the reason
is time. A dashboard figure can be re-checked against the definition sitting
beside it. A spreadsheet is quoted months later, by which point an exporter with
its own copy of a population rule would have drifted from the registry and both
would still be returning plausible numbers — with the wrong one in the
attachment, where nobody can see the definition it used.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from lnd.export.provenance import Stamp, stamp
from lnd.metrics import drilldown, registry
from lnd.metrics.filters import MetricFilters

#: Cell values a writer is expected to handle. Dates are in the list because
#: drill-through rows carry them — an attendance date, a programme's start —
#: and a writer that had not been told so would stringify them by accident.
Cell = str | int | float | Decimal | bool | date | datetime | None


@dataclass(frozen=True)
class ExportTable:
    """One sheet's worth of export: what it is, what is in it, and its scope."""

    #: Used as the file stem and the worksheet name.
    name: str
    title: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Cell, ...], ...]
    stamp: Stamp


def kpi_table(session: Session, filters: MetricFilters) -> ExportTable:
    """Every metric the filters allow, one row each.

    The formatted string is exported beside the raw value on purpose. The raw
    value is what a spreadsheet can compute with; the formatted one is what the
    dashboard showed, and it carries the unit — +88.2 is an NPS index and 88.2%
    is a percentage, and a column of bare numbers loses exactly that distinction.
    """
    values = list(registry.compute_all(session, filters))
    columns = (
        "metric",
        "value",
        "formatted",
        "unit",
        "numerator",
        "denominator",
        "sample_size",
        "provenance",
        "definition",
        "counted_over",
        "excludes",
        "note",
    )
    rows = tuple(
        (
            value.title,
            value.value,
            value.formatted(),
            value.unit.value,
            value.numerator,
            value.denominator,
            value.sample_size,
            value.provenance.value,
            value.definition,
            value.population.description,
            "; ".join(value.population.excludes),
            registry.get(value.key).spec.note or "",
        )
        for value in values
    )
    return ExportTable(
        name="kpis",
        title="Key figures",
        columns=columns,
        rows=rows,
        stamp=stamp(session, filters, values),
    )


def records_table(session: Session, metric_key: str, filters: MetricFilters) -> ExportTable:
    """The rows behind one metric.

    `drilldown.rows_behind` takes its grain from the metric's own population, so
    an exported record list is by construction the list the figure was computed
    over. A row set assembled here instead would be a second definition of the
    population — the failure the drill-through layer exists to prevent, written
    to a file where it outlives the screen.
    """
    metric = registry.compute(metric_key, session, filters)
    opened = drilldown.rows_behind(metric_key, session, filters, limit=drilldown.MAX_LIMIT)
    rows = tuple(tuple(row.get(column) for column in opened.columns) for row in opened.rows)
    return ExportTable(
        name=metric_key,
        title=f"{metric.title} — {opened.total} {opened.grain.value} rows",
        columns=opened.columns,
        rows=rows,
        stamp=stamp(session, filters, [metric]),
    )


def as_dicts(table: ExportTable) -> list[dict[str, Any]]:
    """The rows as mappings. For tests and for anything that prefers names."""
    return [dict(zip(table.columns, row, strict=True)) for row in table.rows]


__all__ = ["Cell", "ExportTable", "as_dicts", "kpi_table", "records_table"]
