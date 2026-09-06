"""The metric layer: one definition per KPI, consumed identically everywhere.

`registry.METRICS` is the catalogue — twenty-one entries, each declaring what it
means, what population it counts over, which filters it honours, and how it
relates to the figure the workbook published. The API, the exports and the
golden-value suite all read it, so there is exactly one definition of any number
this platform reports.
"""

from __future__ import annotations

from lnd.metrics.base import MetricSpec, MetricValue, Provenance, Ratio, Unit
from lnd.metrics.filters import Dimension, MetricFilters, UnsupportedFilter
from lnd.metrics.population import Grain, Population
from lnd.metrics.registry import (
    BY_KEY,
    METRICS,
    UnknownMetric,
    by_provenance,
    compute,
    compute_all,
    get,
)

__all__ = [
    "BY_KEY",
    "METRICS",
    "Dimension",
    "Grain",
    "MetricFilters",
    "MetricSpec",
    "MetricValue",
    "Population",
    "Provenance",
    "Ratio",
    "Unit",
    "UnknownMetric",
    "UnsupportedFilter",
    "by_provenance",
    "compute",
    "compute_all",
    "get",
]
