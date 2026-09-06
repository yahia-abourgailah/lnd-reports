"""The metrics, computed against a known dataset.

Built from one program with a shape chosen so that every correction the platform
makes is visible in the arithmetic: two enrolled people of whom one attends, two
sessions, one survey response, and one employee who has left the roster.

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
from lnd.ingest.landing import land
from lnd.ingest.models import Entity, Source
from lnd.metrics import drilldown
from lnd.metrics.filters import Dimension, MetricFilters, UnsupportedFilter
from lnd.models.app_ import SurveyQuestionMap
from lnd.models.core import DimEmployee, EvaluationDimension
from lnd.transform.runner import transform_programs

pytestmark = pytest.mark.usefixtures("core_db")

FEBRUARY = dt.date(2026, 2, 2)


def user(odoo_id: int, code: str, name: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": odoo_id,
        "odoo_id": str(odoo_id),
        "employee_code": code,
        "name": name,
        "full_name": name,
        "email": f"{code.lower()}@example.com",
        "sector": "Commercial",
        "status": "active",
        "company": {"id": 1, "name": "The Address Investments"},
        "department": {"id": 5, "name": "Sales"},
        "position": {"id": 9, "name": "Consultant"},
        "job_level_name": "Senior",
        "job_level_grade": "9",
    }
    payload.update(overrides)
    return payload


ATTENDED = user(4001, "TAI-1001", "Nour Hassan")
NO_SHOW = user(4002, "TAI-1002", "Kareem Adel")
LEAVER = user(4003, "TAI-1003", "Salma Fouad")

SURVEY = {
    "id": 2,
    "title": "Workshop Evaluation",
    "questions": [
        {"id": 3, "title": "How relevant?", "answer_type": "rating", "required": True},
        {"id": 7, "title": "Recommend?", "answer_type": "rating", "required": True},
    ],
}


def program(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": 93,
        "title": "The Adaptive Leader",
        "status": "upcoming",
        "computed_status": "completed",
        "type": "internal",
        "target": "public",
        "capacity": 4,
        "start_date": "2026-02-02",
        "end_date": "2026-02-03",
        "track": None,
        "parent": None,
        "created_at": "2026-01-02T17:00:02+03:00",
        "updated_at": "2026-01-05T17:00:02+03:00",
        "survey": SURVEY,
        "sessions": [
            {
                "id": 501,
                "session_date": "2026-02-02",
                "session_time_from": "11:00:00",
                "session_time_to": "13:00:00",
                "trainer_name": "Ahmed Elshiaty",
                "location": {"id": 1, "name": "L&D Room"},
                "attendance": [
                    {
                        "id": 9001,
                        "user_odoo_id": "4001",
                        "user": ATTENDED,
                        "attended_at": "2026-02-02T12:00:00+03:00",
                    },
                    {
                        "id": 9002,
                        "user_odoo_id": "4003",
                        "user": LEAVER,
                        "attended_at": "2026-02-02T12:00:00+03:00",
                    },
                ],
            },
            {
                "id": 502,
                "session_date": "2026-02-03",
                "session_time_from": "10:00:00",
                "session_time_to": "13:00:00",
                "trainer_name": "Ahmed Elshiaty",
                "location": {"id": 1, "name": "L&D Room"},
                "attendance": [],
            },
        ],
        "users": [
            {
                "user_odoo_id": "4001",
                "user": ATTENDED,
                "is_enrolled": True,
                "enrolled_at": "2026-02-01T09:00:00+03:00",
                "attendance_rate": 50,
                "survey_answers": [
                    {
                        "id": 1,
                        "question_id": 3,
                        "answer": "5",
                        "answer_type": "rating",
                        "answered_at": "2026-02-03T10:00:00+03:00",
                        "selected_option": None,
                    },
                    {
                        "id": 2,
                        "question_id": 7,
                        "answer": "10",
                        "answer_type": "rating",
                        "answered_at": "2026-02-03T10:00:00+03:00",
                        "selected_option": None,
                    },
                ],
                "assessment_answers": [],
            },
            {
                "user_odoo_id": "4002",
                "user": NO_SHOW,
                "is_enrolled": True,
                "enrolled_at": "2026-02-01T09:00:00+03:00",
                "attendance_rate": 0,
                "survey_answers": [],
                "assessment_answers": [],
            },
        ],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def loaded(core_db: Session) -> Session:
    """One program transformed, with the question map seeded and one leaver.

    The leaver is the important part of the setup. They attended, so they are in
    the numerator's raw material; they are not on the roster, so they must not
    be in the denominator — and they must not be in the numerator either, which
    is the correction P-13 names.
    """
    # The mapping is seeded by migration 0009, and `core_db` truncates it along
    # with everything else — so it is restored here rather than invented. Using
    # the same five rows the migration ships means these tests exercise the
    # mapping that production will actually run on.
    for question_id, dimension, low, high in (
        (3, EvaluationDimension.KNOWLEDGE_RELEVANCE, 1, 5),
        (7, EvaluationDimension.RECOMMEND, 0, 10),
    ):
        core_db.add(
            SurveyQuestionMap(
                crm_survey_id=2,
                crm_question_id=question_id,
                dimension=dimension,
                scale_min=low,
                scale_max=high,
                authored_by="tests",
            )
        )
    core_db.flush()

    land(core_db, source=Source.CRM, entity=Entity.PROGRAM, records=[("93", program())])
    transform_programs(core_db)

    # Salma has left. The roster no longer returns her, and only this flag keeps
    # her attendance from being counted against a population she is not in.
    core_db.query(DimEmployee).filter(DimEmployee.odoo_id == "4003").update(
        {"on_current_roster": False}
    )
    core_db.flush()
    return core_db


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
