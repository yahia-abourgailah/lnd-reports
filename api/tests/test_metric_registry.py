"""The registry as a contract.

These need no database. They pin the things that must be true of the catalogue
itself — that there are twenty-one metrics, that each declares a population and
a definition, and that a metric refuses a filter it does not define rather than
answering a different question with a familiar-looking number.
"""

from __future__ import annotations

import pytest

from lnd import metrics
from lnd.metrics import definitions as d
from lnd.metrics.base import Provenance, Ratio, Unit
from lnd.metrics.filters import Dimension, MetricFilters, UnsupportedFilter


class TestCatalogue:
    def test_there_are_twenty_one(self) -> None:
        """The number in the plan. A metric quietly dropped from the registry is
        a metric nobody can see is missing."""
        assert len(metrics.METRICS) == 21

    def test_the_provenance_split_matches_the_plan(self) -> None:
        """Three unchanged, five restated or renamed, six corrected, seven new.

        Pinned because it is what the reconciliation statement is built from:
        every restated and corrected figure has to be walked through with L&D,
        and a metric that changed its provenance without anybody noticing is a
        conversation that does not happen.
        """
        counts = {p: len(metrics.by_provenance(p)) for p in Provenance}
        assert counts[Provenance.UNCHANGED] == 3
        assert counts[Provenance.RESTATED] + counts[Provenance.RENAMED] == 5
        assert counts[Provenance.CORRECTED] == 5
        assert counts[Provenance.NEW] == 8

    def test_every_key_is_unique(self) -> None:
        keys = [m.spec.key for m in metrics.METRICS]
        assert len(keys) == len(set(keys))

    def test_every_metric_carries_a_definition(self) -> None:
        """It ships to the tooltip and stamps every export. The number, its
        definition and its population travel together or not at all."""
        for metric in metrics.METRICS:
            assert metric.spec.definition.strip()

    def test_every_metric_declares_a_population(self) -> None:
        """FR-A05c. A metric with no declared scope is a metric whose scope is
        whatever the last filter happened to be — which is P-03."""
        for metric in metrics.METRICS:
            assert metric.spec.population.description.strip()

    def test_every_changed_metric_explains_itself(self) -> None:
        """Anything restated, renamed or corrected needs a sentence somebody can
        read out in the stakeholder walkthrough."""
        for metric in metrics.METRICS:
            if metric.spec.provenance in {
                Provenance.RESTATED,
                Provenance.RENAMED,
                Provenance.CORRECTED,
            }:
                assert metric.spec.note.strip(), f"{metric.spec.key} changed without a note"

    def test_an_unknown_key_names_the_known_ones(self) -> None:
        with pytest.raises(metrics.UnknownMetric, match="total_programs"):
            metrics.get("total_progams")


class TestScopeIsDeclaredNotInherited:
    def test_a_metric_refuses_a_dimension_it_does_not_define(self) -> None:
        """The defence that actually prevents P-03.

        Participation Rate has no trainer population. Answering anyway would
        narrow the numerator by trainer and leave the denominator whole, which
        is a smaller number wearing the same label — exactly the shape of the
        published error.
        """
        with pytest.raises(UnsupportedFilter, match="trainer"):
            d.PARTICIPATION_RATE.spec.reject_unsupported(MetricFilters(trainer_keys=frozenset({1})))

    def test_the_refusal_says_what_the_metric_does_count(self) -> None:
        with pytest.raises(UnsupportedFilter, match="active employees"):
            d.PARTICIPATION_RATE.spec.reject_unsupported(MetricFilters(trainer_keys=frozenset({1})))

    def test_a_program_metric_refuses_a_learner_dimension(self) -> None:
        """A program has no sector. Filtering programs by sector would silently
        mean "programs somebody from that sector attended", which is a different
        question wearing the same label."""
        with pytest.raises(UnsupportedFilter, match="sector"):
            d.TOTAL_PROGRAMS.spec.reject_unsupported(MetricFilters(sectors=frozenset({"Finance"})))

    def test_no_filters_is_never_a_refusal(self) -> None:
        for metric in metrics.METRICS:
            metric.spec.reject_unsupported(MetricFilters())


class TestFilters:
    def test_an_empty_filter_set_says_so(self) -> None:
        assert MetricFilters().describe() == "no filters — the full population"

    def test_the_dimensions_used_are_reported(self) -> None:
        """A number that was filtered and one that was not must never look alike
        on screen."""
        filters = MetricFilters(sectors=frozenset({"Finance"}))
        assert filters.dimensions_used == {Dimension.SECTOR}

    def test_clearing_a_dimension_leaves_the_others(self) -> None:
        filters = MetricFilters(
            sectors=frozenset({"Finance"}),
            date_from=None,
            companies=frozenset({"The MarQ Communities"}),
        )
        cleared = filters.without(Dimension.SECTOR)

        assert cleared.sectors == frozenset()
        assert cleared.companies == frozenset({"The MarQ Communities"})

    def test_clearing_does_not_mutate_the_original(self) -> None:
        """Frozen so a filter cannot be added downstream of the metric that
        validated it — the mechanism by which a hidden filter comes back."""
        filters = MetricFilters(sectors=frozenset({"Finance"}))
        filters.without(Dimension.SECTOR)

        assert filters.sectors == frozenset({"Finance"})


class TestRatio:
    def test_two_groups_add_both_terms(self) -> None:
        """The whole reason RatioMetric exists. 100% over 2 responses and 80%
        over 98 combine to 80.4%, not to the 90% that averaging would give."""
        combined = Ratio(numerator=2, denominator=2) + Ratio(numerator=78, denominator=98)

        assert combined.numerator == 80
        assert combined.denominator == 100
        assert combined.value is not None
        assert round(combined.value * 100, 1) == 80.0

    def test_a_zero_denominator_is_undefined_not_zero(self) -> None:
        """ "No enrollments, so no no-show rate" is not "a no-show rate of 0%"."""
        assert Ratio(numerator=0, denominator=0).value is None


class TestFormatting:
    def test_nps_is_signed_and_not_a_percentage(self) -> None:
        """The workbook reported 92.7%, which is not what NPS is. Shown beside
        +88 without saying so it reads as a fall, and it is not: the earlier
        figure was a different calculation on a different scale."""
        assert d.NPS.spec.unit is Unit.NPS

    def test_an_undefined_value_shows_as_a_dash(self) -> None:
        """Never as zero. Zero is a measurement."""
        from lnd.metrics.base import MetricValue

        value = MetricValue(
            key="k",
            title="t",
            definition="d",
            population=d.NPS.spec.population,
            provenance=Provenance.NEW,
            unit=Unit.PERCENT,
            value=None,
        )
        assert value.formatted() == "—"
        assert value.is_defined is False


class TestPendingSources:
    def test_the_linkedin_metrics_are_declared_not_omitted(self) -> None:
        """ "We never built LinkedIn reporting" should be a line on the dashboard
        from day one, not a discovery in month six."""
        for key in ("linkedin_hours", "blended_learner_hours", "unique_reach"):
            assert key in metrics.BY_KEY

    def test_they_report_no_value_rather_than_zero(self) -> None:
        """There has been no measurement, which is not a measurement of none."""
        for key in ("linkedin_hours", "blended_learner_hours", "unique_reach"):
            assert metrics.BY_KEY[key].spec.note
