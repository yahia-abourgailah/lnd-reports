"""Scorecards, coverage and the funnel, against the same known dataset.

The dataset is `tests.fixtures.metrics_dataset`, the same one the metric tests
run against: one programme, two enrolled people of whom one attends, one survey
response, and a leaver who attended and is no longer on the roster. Small enough
to check by hand, which is what these assertions do.

What is being tested here is almost never arithmetic — the arithmetic is the
registry's and is tested there. It is that these views ask the registry rather
than recompute, and that the numbers they show line up with the rows they open
to. A scorecard whose total disagrees with the same metric filtered to that
programme is the failure mode this week could most easily have introduced.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from lnd.analysis import coverage as coverage_service
from lnd.analysis import funnel as funnel_service
from lnd.analysis import scorecards
from lnd.metrics import registry
from lnd.metrics.filters import Dimension, EmptyScope, MetricFilters
from lnd.models.core import DimTrainer
from lnd.transform import trainer_kind

# `loaded` comes from conftest: the same programme the metric tests are checked
# against. These views claim to show the registry's own figures, so they have to
# be checked against the registry's own numbers.

pytestmark = pytest.mark.usefixtures("core_db")

PROGRAM_ID = 93


def figure(card_figures: tuple, key: str):  # type: ignore[type-arg]
    return next(f for f in card_figures if f.key == key)


class TestProgramScorecard:
    """Every figure is `registry.compute`, and the sessions add up to it."""

    def test_each_figure_equals_the_same_metric_filtered_to_the_programme(
        self, loaded: Session
    ) -> None:
        card = scorecards.program_scorecard(loaded, PROGRAM_ID)
        pinned = MetricFilters().narrowed_to(Dimension.PROGRAM, str(PROGRAM_ID))

        assert card.figures, "a scorecard with no figures is not a scorecard"
        for shown in card.figures:
            expected = registry.compute(shown.key, loaded, pinned)
            assert shown.value == expected.value, shown.key
            assert shown.numerator == expected.numerator, shown.key
            assert shown.denominator == expected.denominator, shown.key

    def test_the_head_counts_sum_to_the_participants_figure(self, loaded: Session) -> None:
        """The session column and the figure above it come from one scope.

        Not a tautology: the counts are grouped from `scope.attendances` while
        the figure counts distinct people over the same scope, so they agree
        here only because nobody in this dataset attends two sessions. What the
        assertion protects is the join — a session count built from the raw
        attendance table rather than the scoped one would include the leaver.
        """
        card = scorecards.program_scorecard(loaded, PROGRAM_ID)
        attendances = sum(row.attendees for row in card.sessions)
        assert attendances == figure(card.figures, "learner_hours").sample_size

    def test_the_comments_are_whole_and_unattributed(self, loaded: Session) -> None:
        card = scorecards.program_scorecard(loaded, PROGRAM_ID)
        for comment in card.comments:
            assert comment.text == comment.text.strip()
            assert not hasattr(comment, "name")

    def test_an_unknown_programme_is_not_an_empty_scorecard(self, loaded: Session) -> None:
        """404, never a card of unfiltered figures under a title that is absent."""
        with pytest.raises(scorecards.UnknownProgram):
            scorecards.program_scorecard(loaded, 999_999)


class TestTrainerScorecard:
    def test_delivery_figures_are_narrowed_by_trainer(self, loaded: Session) -> None:
        trainer_key = loaded.query(DimTrainer.trainer_key).scalar()
        card = scorecards.trainer_scorecard(loaded, trainer_key)
        pinned = MetricFilters().narrowed_to(Dimension.TRAINER, str(trainer_key))

        for shown in card.delivery:
            assert shown.value == registry.compute(shown.key, loaded, pinned).value, shown.key

    def test_nps_is_weighted_by_responses_not_averaged(self, loaded: Session) -> None:
        """The parts sum to the whole; their percentages do not average to it.

        100% over 2 responses and 80% over 98 combine to 80.4%. The combined
        figure is the registry's own answer over the set of programmes, and each
        contribution is the same metric under a narrower filter, so this holds
        by construction — and fails loudly if anybody ever computes the headline
        by averaging the rows beneath it.
        """
        trainer_key = loaded.query(DimTrainer.trainer_key).scalar()
        card = scorecards.trainer_scorecard(loaded, trainer_key)
        combined = figure(card.programme_level, "nps")

        numerators = sum(Decimal(c.value.numerator or 0) for c in card.nps_by_program)
        denominators = sum(Decimal(c.value.denominator or 0) for c in card.nps_by_program)
        assert numerators == combined.numerator
        assert denominators == combined.denominator

    def test_a_trainer_with_nothing_in_scope_reports_nothing(self, loaded: Session) -> None:
        """Not the platform's figures under that person's name.

        The empty case is the dangerous one: an empty programme set means "not
        filtered" to `MetricFilters`, so without the guard a trainer who
        delivered nothing in the filtered period would show the whole
        platform's NPS.
        """
        trainer_key = loaded.query(DimTrainer.trainer_key).scalar()
        elsewhere = MetricFilters(program_ids=frozenset({999_999}))
        card = scorecards.trainer_scorecard(loaded, trainer_key, elsewhere)
        assert card.program_ids == ()
        assert card.programme_level == ()
        assert card.nps_by_program == ()

    def test_an_empty_overlap_raises_rather_than_widening(self) -> None:
        with pytest.raises(EmptyScope):
            MetricFilters(program_ids=frozenset({1})).narrowed_within(
                Dimension.PROGRAM, frozenset({2})
            )

    def test_the_index_marks_what_is_not_a_person(self, loaded: Session) -> None:
        rows = scorecards.trainer_index(loaded)
        assert rows, "every trainer in the dimension is listed"
        assert {"sessions", "programs", "attendances"} <= set(rows[0])
        assert all("is_placeholder" in row and "is_external" in row for row in rows)


class TestTrainerKind:
    def test_the_placeholder_and_the_vendor_are_told_apart(self) -> None:
        assert trainer_kind.classify("L&D Team") == (True, False)
        assert trainer_kind.classify("Belton Academy") == (False, True)

    def test_an_unrecognised_name_is_a_person(self) -> None:
        """The direction the failure falls is deliberate.

        An unclassified vendor appears as a person, which somebody notices; a
        keyword rule that read "Nadia Akademi" as an organisation would mislabel
        a real trainer, which nobody would.
        """
        assert trainer_kind.classify("Nadia Akademi") == (False, False)
        assert trainer_kind.classify("  l&d  TEAM ") == (True, False)
        assert trainer_kind.classify(None) == (False, False)


class TestCoverage:
    def test_the_slices_sum_to_the_whole(self, loaded: Session) -> None:
        """Both terms, not the percentages. `RatioMetric` is why this holds."""
        result = coverage_service.coverage(loaded, Dimension.COMPANY)
        numerators = sum(Decimal(s.participation.numerator or 0) for s in result.slices)
        denominators = sum(Decimal(s.participation.denominator or 0) for s in result.slices)

        assert numerators == result.overall_participation.numerator
        assert denominators == result.overall_participation.denominator

    def test_the_untrained_counts_sum_to_the_coverage_gap(self, loaded: Session) -> None:
        result = coverage_service.coverage(loaded, Dimension.COMPANY)
        assert sum(Decimal(s.untrained.value or 0) for s in result.slices) == (
            result.overall_untrained.value
        )

    def test_the_tail_is_named_rather_than_truncated(self, loaded: Session) -> None:
        result = coverage_service.coverage(loaded, Dimension.DEPARTMENT)
        assert result.tail.values_total == result.tail.values_shown + result.tail.values_omitted

    def test_the_list_length_equals_the_metric(self, loaded: Session) -> None:
        """The one assertion the coverage screen rests on.

        1,268 names under a heading that says 1,268 — because the list and the
        count are the same statement, not two statements that agree today.
        """
        scoped = MetricFilters(companies=frozenset({"The Address Investments"}))
        listed = coverage_service.untrained(loaded, scoped)
        gap = registry.compute("coverage_gap", loaded, scoped)
        assert listed.total == gap.value
        assert len(listed.rows) == listed.total

    def test_the_names_are_withheld_until_a_scope_is_chosen(self, loaded: Session) -> None:
        """A judgement, not a limit — and the count is never withheld.

        The figure is what a planning conversation needs. A company-wide
        sortable roster of people who have done nothing is a different document
        for a different meeting, and the difference is not technical.
        """
        ungated = coverage_service.untrained(loaded, MetricFilters())
        assert ungated.gated is True
        assert ungated.rows == ()
        assert ungated.total > 0

        scoped = coverage_service.untrained(loaded, MetricFilters(departments=frozenset({"Sales"})))
        assert scoped.gated is False
        assert scoped.rows

    def test_a_period_alone_does_not_unlock_the_names(self, loaded: Session) -> None:
        """ "Everyone untrained since January" is still everyone."""
        import datetime as dt

        result = coverage_service.untrained(loaded, MetricFilters(date_from=dt.date(2026, 1, 1)))
        assert result.gated is True


class TestFunnel:
    def test_the_stages_are_one_person_on_one_programme(self, loaded: Session) -> None:
        """Attendance is per session; the funnel is not.

        Two sessions and two attendances by two people give two attendees. A
        stage built by counting attendance rows would report more people
        attending than the programme has enrolled the moment anybody sits in
        two sessions.
        """
        result = funnel_service.funnel(loaded)
        enrolled, attended, evaluated = result.steps
        assert enrolled.count == 2
        # Two people attended: one of the two enrolled, plus the leaver, who
        # attended without enrolling and is a walk-in. She is counted here and
        # not in Participation Rate, and that is not an inconsistency — the
        # funnel asks what happened on the programme, and it happened.
        assert attended.count == 2
        assert result.walk_ins == 1
        assert evaluated.count == 1

    def test_the_drop_is_the_metric_not_a_subtraction(self, loaded: Session) -> None:
        """`enrolled - attended` counts a walk-in against a genuine no-show."""
        result = funnel_service.funnel(loaded)
        enrolled = result.steps[0]
        assert enrolled.drop_count == result.no_show_rate.numerator
        assert enrolled.count == result.no_show_rate.denominator

    def test_the_response_rate_denominator_is_the_attended_stage(self, loaded: Session) -> None:
        result = funnel_service.funnel(loaded)
        assert result.steps[1].count == result.survey_response_rate.denominator
        assert result.steps[2].count == result.survey_response_rate.numerator

    def test_every_stage_opens_to_its_own_rows(self, loaded: Session) -> None:
        result = funnel_service.funnel(loaded)
        by_stage = {step.stage: step.count for step in result.steps}
        for stage, count in by_stage.items():
            rows = funnel_service.stage_rows(loaded, stage)
            assert rows.total == count, stage
            assert len(rows.rows) == count, stage

    def test_walk_ins_are_reported_rather_than_netted_off(self, loaded: Session) -> None:
        result = funnel_service.funnel(loaded)
        enrolled, attended, _ = result.steps
        no_shows = enrolled.drop_count or 0
        # The identity that holds whatever the walk-ins are. Stated as an
        # equation rather than assumed by subtracting one stage from the next.
        assert attended.count == enrolled.count - no_shows + result.walk_ins
