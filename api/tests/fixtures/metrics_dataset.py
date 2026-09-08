"""The dataset every metric test is checked against.

One programme, shaped so that every correction the platform makes is visible in
the arithmetic: two enrolled people of whom one attends, two sessions, one
survey response, and one employee who has left the roster. Small enough to check
by hand, which is the point — nine KPIs currently live inside GETPIVOTDATA
strings nobody can check by hand, and six of them are wrong.

It lives here rather than beside the metric tests because the week-7 views are
checked against the same numbers. Two datasets would mean a scorecard test that
passes against a shape the metric tests never see.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.orm import Session

from lnd.ingest.landing import land
from lnd.ingest.models import Entity, Source
from lnd.models.app_ import SurveyQuestionMap
from lnd.models.core import DimEmployee, EvaluationDimension
from lnd.transform.runner import transform_programs

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


def build(core_db: Session) -> Session:
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
