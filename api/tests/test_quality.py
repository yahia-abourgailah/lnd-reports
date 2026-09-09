"""The rule catalogue, and completeness per period.

Two claims worth testing and one worth testing hardest.

The catalogue is the single description of each data-quality rule — disposition,
meaning, cost, fix — and the transform, the API and the console all read it. So
the tests here are mostly about it *staying* single: a rule added to the enum
and not described would reach the console as a bare identifier, and a
disposition declared twice would let the exclusion banner disagree with the
console explaining it.

Completeness per period is the harder one, because it is derived rather than
stored. An exception belongs to the month its programme ran in, and that comes
from `dim_program` through the same predicate `scope.programs` uses — so a
figure scoped one way and described another is exactly what these assert
against.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy.orm import Session

from lnd.enrichment.service import TABLES, OverlayKind
from lnd.metrics import aggregate
from lnd.metrics.filters import MetricFilters
from lnd.models.ops import DqDisposition, DqRule
from lnd.quality import catalogue
from lnd.quality import completeness as quality
from lnd.transform import exceptions as transform_exceptions

pytestmark = pytest.mark.usefixtures("core_db")


class TestTheCatalogueIsComplete:
    def test_every_rule_is_described(self) -> None:
        """Enforced at import too, but asserted here so the failure names the rule.

        A rule with no entry reaches the console as an identifier with no
        explanation and no suggested fix — visible, and useless.
        """
        assert set(catalogue.RULES) == set(DqRule)

    def test_every_rule_says_what_to_do(self) -> None:
        """Always populated. "Nothing here can fix this" is an answer; a blank
        is an operator leaving the row in the queue."""
        for rule, spec in catalogue.RULES.items():
            assert spec.resolution.strip(), rule.value
            assert spec.means.strip(), rule.value
            assert spec.costs.strip(), rule.value

    def test_every_claimed_fix_is_a_route_that_exists(self) -> None:
        """A rule offering a form the enrichment API cannot serve is worse than
        one offering none: somebody follows it and finds nothing there."""
        for rule, spec in catalogue.RULES.items():
            if spec.fixed_by is not None:
                assert isinstance(spec.fixed_by, OverlayKind), rule.value
                assert spec.fixed_by in TABLES, rule.value

    def test_the_transform_reads_the_catalogues_dispositions(self) -> None:
        """One list, not two.

        Two spellings of "which rules cost numbers" is how one of them comes to
        disagree with the exclusion banner — which reported three flagged
        records as three excluded ones for a week.
        """
        assert transform_exceptions.DISPOSITIONS is catalogue.DISPOSITIONS
        assert set(transform_exceptions.DISPOSITIONS) == set(DqRule)

    def test_costs_numbers_means_quarantined(self) -> None:
        for spec in catalogue.RULES.values():
            assert spec.costs_numbers == (spec.disposition is DqDisposition.QUARANTINED)


class TestCompleteness:
    def test_the_banner_and_the_console_read_one_function(self, loaded: Session) -> None:
        """`aggregate.completeness` is what every API envelope calls, and it
        delegates. Two implementations would let a screen disagree with the
        console that exists to explain it."""
        headline = aggregate.completeness(loaded)
        detailed = quality.completeness(loaded)
        assert (headline.excluded, headline.flagged) == (detailed.excluded, detailed.flagged)

    def test_the_split_is_by_disposition(self, loaded: Session) -> None:
        scoped = quality.completeness(loaded)
        assert scoped.excluded == sum(
            entry.count for entry in scoped.by_rule if entry.costs_numbers
        )
        assert scoped.flagged == sum(
            entry.count for entry in scoped.by_rule if not entry.costs_numbers
        )
        assert scoped.total == scoped.excluded + scoped.flagged

    def test_losses_are_listed_before_notices(self, loaded: Session) -> None:
        """An operator reading a queue should meet the rules that cost figures
        before the ones that do not, whatever their size."""
        entries = quality.completeness(loaded).by_rule
        costs = [entry.costs_numbers for entry in entries]
        assert costs == sorted(costs, reverse=True)

    def test_a_period_with_no_programmes_holds_nothing(self, loaded: Session) -> None:
        empty = MetricFilters(date_from=dt.date(2001, 1, 1), date_to=dt.date(2001, 12, 31))
        scoped = quality.completeness(loaded, empty)
        assert scoped.total == 0

    def test_the_months_come_from_the_data(self, loaded: Session) -> None:
        """Not from a typed range. A trend over months with no programmes draws
        a run of zeroes that reads as a collapse in delivery."""
        months = quality.months_in_scope(loaded)
        assert months
        assert months == sorted(months)

    def test_the_trend_uses_the_same_function_as_the_headline(self, loaded: Session) -> None:
        """Each month is the same computation under a narrower window, so the
        two cannot drift. A grouped second implementation would agree the day
        it was written."""
        for month in quality.trend(loaded).months:
            year, number = (int(part) for part in month.period.split("-"))
            window = MetricFilters(
                date_from=dt.date(year, number, 1),
                date_to=dt.date(year, number, 28),
            )
            direct = quality.completeness(loaded, window)
            assert (month.excluded, month.flagged) == (direct.excluded, direct.flagged)
