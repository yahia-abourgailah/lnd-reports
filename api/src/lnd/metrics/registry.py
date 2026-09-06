"""The metric registry: one definition per KPI, looked up by key.

Nine of these lived inside nine unauditable GETPIVOTDATA strings, six of them
wrong. The registry is what replaces that — a metric is defined once, and the
API, the exports and the golden-value suite all read the same declaration. There
is no second definition available to disagree with the first.

The invariants below are asserted at import, so a duplicate key or a metric
missing its definition fails the process rather than the dashboard.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from lnd.metrics import definitions as d
from lnd.metrics.base import Metric, MetricValue, Provenance
from lnd.metrics.filters import MetricFilters

#: The twenty-one, in the order the reconciliation statement walks them:
#: unchanged first, then restated, corrected, and new. Reading the list top to
#: bottom is reading the change to L&D's reporting in order of how much
#: explaining it needs.
METRICS: tuple[Metric, ...] = (
    # -- unchanged: same definition, same number ---------------------------
    d.TOTAL_PARTICIPANTS,
    d.LEARNER_HOURS,
    d.FACILITATOR_PERFORMANCE,
    # -- restated and renamed ----------------------------------------------
    d.TRAINING_DAYS,
    d.TRAINING_HOURS_DELIVERED,
    d.TOTAL_PROGRAMS,
    d.LND_DELIVERED_SHARE,
    d.PUBLIC_PROGRAM_SHARE,
    # -- corrected: the published figure was wrong -------------------------
    d.KNOWLEDGE_RELEVANCE,
    d.ACTIVITY_EFFECTIVENESS,
    d.LOGISTICS_EFFECTIVENESS,
    d.NPS,
    d.PARTICIPATION_RATE,
    # -- new ----------------------------------------------------------------
    d.NO_SHOW_RATE,
    d.FILL_RATE,
    d.SURVEY_RESPONSE_RATE,
    d.COVERAGE_GAP,
    d.MONTHS_SINCE_LAST_TRAINING,
    d.LINKEDIN_HOURS,
    d.BLENDED_LEARNER_HOURS,
    d.UNIQUE_REACH,
)

BY_KEY: dict[str, Metric] = {metric.spec.key: metric for metric in METRICS}


class UnknownMetric(KeyError):
    """Asked for a metric that is not in the registry."""


def get(key: str) -> Metric:
    try:
        return BY_KEY[key]
    except KeyError:
        raise UnknownMetric(
            f"{key!r} is not a metric. Known keys: {', '.join(sorted(BY_KEY))}"
        ) from None


def compute(key: str, session: Session, filters: MetricFilters | None = None) -> MetricValue:
    return get(key).compute(session, filters or MetricFilters())


def compute_all(session: Session, filters: MetricFilters | None = None) -> Iterator[MetricValue]:
    """Every metric under one set of filters.

    A metric that refuses the filters is skipped rather than raising, because a
    dashboard narrowed to one trainer should show the metrics that mean
    something for a trainer instead of an error page. The refusal is not
    silent — the metric is simply absent, and its absence is the honest answer
    to "what is this trainer's participation rate?", which is that the question
    does not have one.
    """
    from lnd.metrics.filters import UnsupportedFilter

    applied = filters or MetricFilters()
    for metric in METRICS:
        try:
            yield metric.compute(session, applied)
        except UnsupportedFilter:
            continue


def by_provenance(provenance: Provenance) -> tuple[Metric, ...]:
    return tuple(m for m in METRICS if m.spec.provenance is provenance)


def _check_registry() -> None:
    """Fail at import rather than at the first request.

    Three things nobody should be able to get wrong quietly: two metrics sharing
    a key, a metric with no definition to show in a tooltip, and a metric whose
    population was never declared.
    """
    keys = [m.spec.key for m in METRICS]
    duplicates = {key for key in keys if keys.count(key) > 1}
    if duplicates:
        raise RuntimeError(f"duplicate metric keys: {', '.join(sorted(duplicates))}")

    for metric in METRICS:
        spec = metric.spec
        if not spec.definition.strip():
            raise RuntimeError(f"{spec.key} has no definition; it ships with every export")
        if not spec.supports:
            raise RuntimeError(f"{spec.key} declares no dimensions and could never be filtered")


_check_registry()
