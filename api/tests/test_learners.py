"""The ranking and the profile.

The dataset is the shared one: two people attend the same two-hour session, so
they tie on hours — which is the case this module has to get right, and the one
the workbook's hand-typed Top Learner sheet never had to think about.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from lnd.analysis import learners
from lnd.metrics import registry
from lnd.metrics.filters import Dimension, MetricFilters, UnsupportedFilter

pytestmark = pytest.mark.usefixtures("core_db")

SCOPED = MetricFilters(departments=frozenset({"Sales"}))


class TestRanking:
    def test_the_same_query_twice_returns_the_same_order(self, loaded: Session) -> None:
        """The defect a deterministic tiebreak prevents.

        Ordering by hours alone leaves equal rows in whatever order the plan
        produces, so a person drops off a recognition list between two runs over
        identical data. Four groups tie on hours in the live set; two do here.
        """
        first = learners.top_learners(loaded, SCOPED)
        second = learners.top_learners(loaded, SCOPED)

        assert [(r.rank, r.employee_key) for r in first.rows] == [
            (r.rank, r.employee_key) for r in second.rows
        ]

    def test_a_tie_shares_a_rank(self, loaded: Session) -> None:
        """Competition ranking, not sequence numbering.

        Both attendees sat the same two-hour session. Numbering them 1 and 2
        would publish a difference the data does not contain, decided by a
        tiebreak that exists only to make the order stable.
        """
        ranked = learners.top_learners(loaded, SCOPED)
        hours = {row.hours for row in ranked.rows}

        assert len(ranked.rows) == 2
        assert len(hours) == 1, "the fixture's two attendees tie on hours"
        assert [row.rank for row in ranked.rows] == [1, 1]

    def test_the_names_are_withheld_until_a_scope_is_chosen(self, loaded: Session) -> None:
        """The same rule the zero-training list follows, from the other end.

        A ranked, named list of employees by hours is a leaderboard, and nobody
        on it chose to be ranked. The distribution is not the sensitive part and
        is returned either way.
        """
        ungated = learners.top_learners(loaded, MetricFilters())

        assert ungated.gated is True
        assert ungated.rows == ()
        assert ungated.total_learners == 2
        assert ungated.hours_max is not None

    def test_the_ranking_counts_the_same_people_the_metrics_do(self, loaded: Session) -> None:
        """Derived from `scope.attendances`, so it cannot drift from the figure.

        P-09 was a hand-typed ranking whose headline formula pointed at a count
        cell. The fix is not a better formula, it is that nobody types it.
        """
        ranked = learners.top_learners(loaded, SCOPED)
        participants = registry.compute("total_participants", loaded, SCOPED)

        assert ranked.total_learners == participants.value


class TestProfile:
    def test_a_profile_total_equals_the_same_metric_filtered_to_that_person(
        self, loaded: Session
    ) -> None:
        ranked = learners.top_learners(loaded, SCOPED)
        key = ranked.rows[0].employee_key
        profile = learners.profile(loaded, key)
        pinned = MetricFilters().narrowed_to(Dimension.LEARNER, str(key))

        assert profile.figures
        for shown in profile.figures:
            expected = registry.compute(shown.key, loaded, pinned)
            assert shown.value == expected.value, shown.key
            assert shown.numerator == expected.numerator, shown.key

    def test_the_programme_table_sums_to_the_hours_figure(self, loaded: Session) -> None:
        ranked = learners.top_learners(loaded, SCOPED)
        profile = learners.profile(loaded, ranked.rows[0].employee_key)
        hours = next(f for f in profile.figures if f.key == "learner_hours")

        assert sum(Decimal(str(p.hours or 0)) for p in profile.programs) == hours.value

    def test_a_roster_metric_refuses_the_learner_dimension(self, loaded: Session) -> None:
        """One person's participation rate is 1/1, which is not a rate.

        Answering anyway would be P-13 at the grain of an individual: a
        numerator narrowed to somebody and a denominator that was not.
        """
        pinned = MetricFilters().narrowed_to(Dimension.LEARNER, "1")

        for key in ("participation_rate", "coverage_gap", "training_days"):
            with pytest.raises(UnsupportedFilter):
                registry.compute(key, loaded, pinned)

    def test_an_unknown_person_is_not_an_empty_profile(self, loaded: Session) -> None:
        with pytest.raises(learners.UnknownLearner):
            learners.profile(loaded, 999_999)


class TestSearch:
    def test_an_empty_query_returns_nothing(self, loaded: Session) -> None:
        """ "Everybody, alphabetically" is the roster the gate exists to withhold."""
        assert learners.find(loaded, "") == []
        assert learners.find(loaded, "   ") == []

    def test_a_name_finds_the_person(self, loaded: Session) -> None:
        found = learners.find(loaded, "nour")
        assert [row["full_name"] for row in found] == ["Nour Hassan"]

    def test_an_employee_code_finds_the_person(self, loaded: Session) -> None:
        assert learners.find(loaded, "TAI-1001")[0]["employee_code"] == "TAI-1001"


class TestTheRankingIsNotABreakdown:
    def test_a_metric_refuses_to_be_sliced_by_learner(self, loaded: Session) -> None:
        """A dimension a metric filters by is not one it can be sliced by.

        Every profile metric honours `learner` as a filter. Slicing by it would
        emit one bar per person — a ranked list of everybody, reached through a
        chart endpoint that has no gate, which is exactly what `/learners/top`
        is careful about.
        """
        from lnd.metrics import aggregate

        with pytest.raises(UnsupportedFilter, match="individual"):
            aggregate.breakdown("nps", loaded, Dimension.LEARNER, MetricFilters())
