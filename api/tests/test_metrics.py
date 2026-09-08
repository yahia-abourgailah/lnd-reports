"""The metrics, computed against a known dataset.

The dataset itself is `tests.fixtures.metrics_dataset`, reached through the
`loaded` fixture in `conftest`: one program with a shape chosen so that every
correction the platform makes is visible in the arithmetic — two enrolled people
of whom one attends, two sessions, one survey response, and one employee who has
left the roster.

The numbers here are small enough to check by hand, which is the point. Nine
KPIs currently live inside GETPIVOTDATA strings that nobody can check by hand,
and six of them are wrong.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from sqlalchemy.orm import Session

from lnd import metrics
from lnd.metrics import drilldown
from lnd.metrics.filters import Dimension, MetricFilters, UnsupportedFilter
from lnd.models.core import DimEmployee

pytestmark = pytest.mark.usefixtures("core_db")


def value(session: Session, key: str, **kwargs: Any) -> Any:
    return metrics.compute(key, session, MetricFilters(**kwargs))


class TestVolumes:
    def test_total_programs_counts_completed_by_computed_status(self, loaded: Session) -> None:
        """`status` says upcoming and `computed_status` says completed. The two
        disagree on 55 of 57 live programs, and counting off `status` gives 2
        where the answer is 55."""
        assert value(loaded, "total_programs").value == 1

    def test_training_days_counts_sessions(self, loaded: Session) -> None:
        assert value(loaded, "training_days").value == 2

    def test_training_hours_delivered_counts_each_session_once(self, loaded: Session) -> None:
        """Two hours plus three, delivered. Not multiplied by who turned up."""
        assert value(loaded, "training_hours_delivered").value == 5

    def test_learner_hours_multiplies_by_attendance(self, loaded: Session) -> None:
        """Two people at the two-hour session is four learner hours. The
        workbook's single 'hours' column was read as both this and the one
        above."""
        assert value(loaded, "learner_hours").value == 4

    def test_total_participants_counts_people_not_attendances(self, loaded: Session) -> None:
        assert value(loaded, "total_participants").value == 2


class TestParticipationRate:
    def test_the_numerator_lives_inside_the_denominator(self, loaded: Session) -> None:
        """P-13, and the single most consequential line in the metric layer.

        Two people attended, but one has left. The denominator is the two who
        remain on the roster, so the answer is 1/2 — not 2/2, which counts an
        attendance from somebody who is not in the population it is divided by.
        """
        result = value(loaded, "participation_rate")

        assert result.numerator == 1
        assert result.denominator == 2
        assert result.value == 50

    def test_the_denominator_is_counted_never_typed(self, loaded: Session) -> None:
        """P-01. The workbook divided by the literal number 192, which does not
        move when the company does."""
        result = value(loaded, "participation_rate")

        assert (
            result.denominator
            == loaded.query(DimEmployee)
            .filter(
                DimEmployee.is_current,
                DimEmployee.on_current_roster,
                DimEmployee.status == "active",
            )
            .count()
        )

    def test_the_period_narrows_the_numerator_only(self, loaded: Session) -> None:
        """Applying the dates to both sides counts only people who attended,
        which is a rate of 100% arrived at honestly and meaning nothing."""
        result = value(
            loaded,
            "participation_rate",
            date_from=dt.date(2026, 3, 1),
            date_to=dt.date(2026, 3, 31),
        )

        assert result.numerator == 0
        assert result.denominator == 2

    def test_it_refuses_a_trainer_filter(self, loaded: Session) -> None:
        with pytest.raises(UnsupportedFilter):
            value(loaded, "participation_rate", trainer_keys=frozenset({1}))


class TestQualityAndNps:
    def test_a_quality_score_is_over_every_response_in_scope(self, loaded: Session) -> None:
        """P-03 was a scope error, not a formula one. One response rating 5
        is 100%, over a denominator of 1 that is returned with it."""
        result = value(loaded, "knowledge_relevance")

        assert result.value == 100
        assert result.sample_size == 1

    def test_the_response_count_travels_with_the_score(self, loaded: Session) -> None:
        """A 98% score over 297 responses and the same over 4 are different
        claims. Only this distinguishes them on screen."""
        assert value(loaded, "knowledge_relevance").sample_size == 1

    def test_nps_is_an_index_not_a_percentage(self, loaded: Session) -> None:
        """One promoter, no detractors: +100 on the -100..+100 scale. The
        workbook's 92.7% was a different calculation on a different scale, and
        the two must not be formatted alike."""
        result = value(loaded, "nps")

        assert result.value == 100
        assert result.formatted() == "+100.0"

    def test_survey_response_rate_compares_like_grains(self, loaded: Session) -> None:
        """One response over two people-on-a-program who attended. Against
        distinct people it would read differently, and against attendances
        differently again."""
        result = value(loaded, "survey_response_rate")

        assert result.numerator == 1
        assert result.denominator == 2


class TestFunnel:
    def test_no_show_rate_uses_the_enrolled_list(self, loaded: Session) -> None:
        """Two enrolled, one attended. The CRM's users[] array also holds
        walk-ins and survey-only respondents; counting it as enrollment would
        inflate the denominator and push this towards 100%."""
        result = value(loaded, "no_show_rate")

        assert result.numerator == 1
        assert result.denominator == 2
        assert result.value == 50

    def test_fill_rate_is_enrollments_over_capacity(self, loaded: Session) -> None:
        assert value(loaded, "fill_rate").value == 50

    def test_coverage_gap_counts_people_with_no_attendance(self, loaded: Session) -> None:
        """The one metric that cannot be derived from attendance data: a person
        with zero attendance appears in no program payload at all."""
        assert value(loaded, "coverage_gap").value == 1


class TestDrillthrough:
    def test_every_metric_can_be_opened(self, loaded: Session) -> None:
        """A metric that cannot be opened is a metric nobody can check, and six
        of the nine turned out to need checking."""
        for metric in metrics.METRICS:
            result = drilldown.rows_behind(metric.spec.key, loaded, limit=5)
            assert result.grain is metric.spec.population.grain

    def test_the_rows_are_the_rows_the_number_used(self, loaded: Session) -> None:
        opened = drilldown.rows_behind("total_programs", loaded)

        assert opened.total == 1
        assert opened.rows[0]["title"] == "The Adaptive Leader"

    def test_the_participation_denominator_is_a_list_of_names(self, loaded: Session) -> None:
        """What makes the rate arguable rather than assertable. 192 could only
        be disputed; a list of names can be checked against payroll."""
        opened = drilldown.rows_behind("participation_rate", loaded)

        assert opened.total == 2
        assert {row["name"] for row in opened.rows} == {"Nour Hassan", "Kareem Adel"}

    def test_a_truncated_list_says_so(self, loaded: Session) -> None:
        """A truncated list that looks complete is worse than no list."""
        opened = drilldown.rows_behind("total_participants", loaded, limit=1)

        assert opened.truncated is True
        assert opened.total > len(opened.rows)

    def test_it_refuses_the_filters_the_metric_refuses(self, loaded: Session) -> None:
        """Showing rows the number was not computed over is a worse lie than
        showing none."""
        with pytest.raises(UnsupportedFilter):
            drilldown.rows_behind(
                "participation_rate", loaded, MetricFilters(trainer_keys=frozenset({1}))
            )


class TestFiltersAreHonoured:
    def test_a_sector_filter_narrows_the_numbers(self, loaded: Session) -> None:
        narrowed = value(loaded, "total_participants", sectors=frozenset({"Nowhere"}))

        assert narrowed.value == 0

    def test_the_applied_filters_are_reported_back(self, loaded: Session) -> None:
        result = value(loaded, "total_participants", sectors=frozenset({"Commercial"}))

        assert "Commercial" in result.filters_applied
        assert Dimension.SECTOR in result.dimensions_filtered

    def test_an_unfiltered_result_says_it_is_unfiltered(self, loaded: Session) -> None:
        result = value(loaded, "total_participants")

        assert result.dimensions_filtered == frozenset()
        assert "full population" in result.filters_applied

    def test_compute_all_skips_metrics_the_filters_do_not_suit(self, loaded: Session) -> None:
        """A trainer dashboard shows the metrics that mean something for a
        trainer. Participation Rate is absent, and that absence is the honest
        answer to a question that does not have one."""
        keys = {
            result.key
            for result in metrics.compute_all(loaded, MetricFilters(trainer_keys=frozenset({1})))
        }

        assert "participation_rate" not in keys
        assert "training_days" in keys
