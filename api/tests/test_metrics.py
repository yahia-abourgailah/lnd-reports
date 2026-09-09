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


class TestEstimatedTravelsWithThePopulation:
    """A figure resting on assumed employment must say so — every one of them.

    The CRM gives no hire date, so somebody it first shows us in September is
    recorded as valid from before the platform existed and counts in August's
    headcount. That is unavoidable and it is why `is_estimated` exists.

    It was carried by Participation Rate and dropped by Coverage Gap, which is
    the same headcount minus attendance, so one of the two presented an assumed
    figure as an exact one. The flag now comes from the declared population
    rather than from each metric remembering, and this asserts that it cannot be
    dropped again.
    """

    def test_every_metric_over_the_roster_reports_it(self, loaded: Session) -> None:
        from lnd.metrics import population as pop
        from lnd.metrics import registry

        over_roster = [
            metric
            for metric in registry.METRICS
            if metric.spec.population is pop.ENROLLABLE_EMPLOYEES
        ]
        assert over_roster, "no metric uses the roster population any more"

        flags = {
            metric.spec.key: registry.compute(metric.spec.key, loaded, MetricFilters()).is_estimated
            for metric in over_roster
        }
        assert len(set(flags.values())) == 1, (
            f"metrics over one population disagree about whether it is estimated: {flags}"
        )

    def test_metrics_over_other_populations_do_not_claim_it(self, loaded: Session) -> None:
        """The flag means something specific. A count of sessions cannot be
        estimated, and saying so would make the word worthless where it is
        true."""
        from lnd.metrics import population as pop
        from lnd.metrics import registry

        for metric in registry.METRICS:
            if metric.spec.population is pop.ENROLLABLE_EMPLOYEES:
                continue
            value = registry.compute(metric.spec.key, loaded, MetricFilters())
            assert value.is_estimated is False, metric.spec.key


class TestMonthsSinceLastTrainingIsAStock:
    """The period names a date to measure at, not a window to count within.

    This one metric asks how stale a population is, and it was scoped like
    every other: the window's start narrowed the people as well as fixing the
    date. Filtered to August it answered 0.6 months — the median over people
    who had trained in August, who had by definition just trained — and the
    monthly trend was a row of near-zeroes that read as a trend and was an
    artefact of the filter. The figure could never exceed the window's own
    length, so a one-month view could never report more than one month however
    stale the company was.

    The dataset attends on 2 February 2026 and never again, which makes the
    distinction visible: a March window contains no attendance at all, so the
    old scoping had nobody to take a median over.
    """

    MARCH_END = dt.date(2026, 3, 31)

    def test_a_window_with_no_attendance_still_has_a_population(self, loaded: Session) -> None:
        """The question is "how long since these people trained", and March is
        a perfectly good time to ask it. Answering `None` because nobody
        trained in March is answering a different question."""
        result = metrics.compute(
            "months_since_last_training",
            loaded,
            MetricFilters(date_from=dt.date(2026, 3, 1), date_to=self.MARCH_END),
        )

        assert result.sample_size > 0
        assert result.value is not None

    def test_the_start_of_the_window_changes_nothing(self, loaded: Session) -> None:
        """Same as-of date, same answer, however far back the window reaches.
        This is the assertion the defect would have failed."""
        bounded = metrics.compute(
            "months_since_last_training",
            loaded,
            MetricFilters(date_from=dt.date(2026, 3, 1), date_to=self.MARCH_END),
        )
        unbounded = metrics.compute(
            "months_since_last_training", loaded, MetricFilters(date_to=self.MARCH_END)
        )

        assert bounded.value == unbounded.value
        assert bounded.sample_size == unbounded.sample_size

    def test_the_answer_may_exceed_the_window(self, loaded: Session) -> None:
        """A one-month window over a company that last trained in February must
        be able to say so. Two months, not the 0.0 that a flow would give."""
        result = metrics.compute(
            "months_since_last_training",
            loaded,
            MetricFilters(date_from=dt.date(2026, 4, 1), date_to=dt.date(2026, 4, 30)),
        )

        assert result.value is not None
        assert result.value > 2

    def test_a_future_as_of_is_answered_not_clamped(self, loaded: Session) -> None:
        """ "How stale will we be at Christmas, if nobody trains" is a fair
        question, and `reference.windows` asks it on purpose: a golden file
        pinned to "today" goes red overnight for no reason. The caller with no
        business asking is the trend, and the trend is where it stops."""
        future = metrics.compute(
            "months_since_last_training",
            loaded,
            MetricFilters(date_to=dt.date.today() + dt.timedelta(days=90)),
        )
        today = metrics.compute("months_since_last_training", loaded, MetricFilters())

        assert future.value is not None and today.value is not None
        assert future.value > today.value

    def test_the_trend_stops_the_month_in_progress_at_today(
        self, loaded: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every point is staleness at its month end, so the month in progress
        would be asked for a date three weeks away and would answer, drawing a
        rise on the newest point that has not happened.

        The dataset attends on 2 February 2026, so standing inside February
        makes the difference three days rather than nothing."""
        from lnd.metrics import aggregate

        mid_month = dt.date(2026, 2, 5)
        monkeypatch.setattr(aggregate, "_today", lambda: mid_month)

        point = aggregate.trend("months_since_last_training", loaded).points[-1]
        as_at_today = metrics.compute(
            "months_since_last_training", loaded, MetricFilters(date_to=mid_month)
        )

        assert point.key == "2026-02"
        assert point.value.value == as_at_today.value

    def test_the_trend_keeps_scheduled_sessions_in_their_month(
        self, loaded: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The clamp is one metric's declaration, not a rule for every window.
        Four sessions in the live dataset are dated after today; they are
        scheduled, and a count of them belongs in the month they fall in."""
        from lnd.metrics import aggregate

        monkeypatch.setattr(aggregate, "_today", lambda: dt.date(2026, 2, 1))

        # Additive metrics only — a count of distinct people does not sum
        # across months, and asserting that it did would pass here by accident
        # and mean nothing.
        for key in ("training_days", "training_hours_delivered"):
            points = aggregate.trend(key, loaded).points
            counted = sum(p.value.value or 0 for p in points)
            assert counted == metrics.compute(key, loaded, MetricFilters()).value, key

    def test_the_caption_says_what_was_measured(self, loaded: Session) -> None:
        """`filters_applied` is printed on the card and stamped on every
        export. Describing a window the metric did not use would put a caption
        on screen that the figure underneath does not answer."""
        result = metrics.compute(
            "months_since_last_training",
            loaded,
            MetricFilters(date_from=dt.date(2026, 3, 1), date_to=self.MARCH_END),
        )

        assert "2026-03-01" not in result.filters_applied
        assert "2026-03-31" in result.filters_applied

    def test_the_other_filters_still_apply(self, loaded: Session) -> None:
        """Dropping the window's start is the only exception. "In Sales, as at
        the end of March" is still a question with an answer, and a department
        nobody trained in still has none."""
        elsewhere = metrics.compute(
            "months_since_last_training",
            loaded,
            MetricFilters(departments=frozenset({"Nowhere"}), date_to=self.MARCH_END),
        )

        assert elsewhere.sample_size == 0
