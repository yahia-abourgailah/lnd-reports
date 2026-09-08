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

    def test_the_ranking_is_returned_without_narrowing(self, loaded: Session) -> None:
        """The requirement, as a test.

        "A top-learners ranking by learning hours, derived automatically,
        replacing the hand-typed sheet" — and the sheet it replaces was
        company-wide. This ranking was gated when it was first built, which
        left a screen headed "Top learners" showing no learners.
        """
        ranking = learners.top_learners(loaded, MetricFilters())

        assert ranking.rows, "the whole-company ranking must not be empty"
        assert ranking.total_learners == 2
        assert all(row.name for row in ranking.rows)

    def test_the_spread_is_over_everybody_not_the_rows_shown(self, loaded: Session) -> None:
        """A median of the visible fifty is a different statistic under the
        same label. It is computed over the ranked population and returned
        beside the rows, not derived from them."""
        one = learners.top_learners(loaded, MetricFilters(), limit=1)
        whole = learners.top_learners(loaded, MetricFilters())

        assert len(one.rows) == 1
        assert one.total_learners == whole.total_learners
        assert one.hours_median == whole.hours_median
        assert one.truncated is True

    def test_truncated_is_false_when_everybody_fits(self, loaded: Session) -> None:
        """It once read "showing the first 0 of 288", which described a
        truncation that had not happened."""
        ranking = learners.top_learners(loaded, MetricFilters())
        assert ranking.truncated is False

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
        """ "Everybody, alphabetically" is a roster, and nobody asked for one."""
        assert learners.find(loaded, "") == []
        assert learners.find(loaded, "   ") == []

    def test_a_name_finds_the_person(self, loaded: Session) -> None:
        found = learners.find(loaded, "nour")
        assert [row.name for row in found] == ["Nour Hassan"]

    def test_an_employee_code_finds_the_person(self, loaded: Session) -> None:
        assert learners.find(loaded, "TAI-1001")[0].employee_code == "TAI-1001"

    def test_a_match_is_named_the_way_a_ranking_row_is(self) -> None:
        """The guard against the bug this replaced.

        `find` returned raw column names — `full_name`, `department_name` —
        while the screen showing the results read `name` and `department`, so
        every match rendered as an empty row with an empty link. Nothing could
        catch it: the route declared `list[dict[str, Any]]`, which is a
        contract neither mypy nor the front end's types can check.

        The two lists sit on one screen and must speak one vocabulary.
        """
        from lnd.api.v1.learners import MatchOut, RankOut

        shared = set(MatchOut.model_fields) - {"rank", "programs", "sessions", "hours"}
        assert shared <= set(RankOut.model_fields), (
            "the search result and the ranking row name the same attributes differently"
        )

    def test_a_match_carries_everything_the_result_line_shows(self, loaded: Session) -> None:
        """Not just the fields the query happened to select.

        `company` was absent from the select, so the subtitle under a search
        hit could never have rendered it even once the names lined up.
        """
        found = learners.find(loaded, "nour")[0]
        assert found.employee_key
        assert found.name
        assert found.employee_code is not None
        assert hasattr(found, "company")


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
