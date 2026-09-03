"""A program payload shaped exactly like §5.1 of the CRM API document.

Kept in one place so every test works against the real response shape rather
than a convenient invention. When the first genuine fixture is recorded against
the CRM, this is what it gets diffed against.
"""

from __future__ import annotations

from typing import Any

USER_AHMED: dict[str, Any] = {
    "id": 38366,
    "odoo_id": "4821",
    "name": "Ahmed Kamal",
    "full_name": "Ahmed Kamal Ibrahim",
    "email": "ahmed.kamal@example.test",
    "mobile": "+201001234567",
    "employee_code": "10422",
    "status": "active",
    "department": {"id": 214413, "odoo_id": "77", "name": "Sales"},
    "company": {"id": 1, "odoo_id": "1", "name": "The Address Investments"},
    "position": {"id": 60, "name": "Senior Property Consultant"},
    "sector": "Commercial ",
    "job_level_name": "Senior Specialist",
    "job_level_grade": "9",
}

USER_SARA: dict[str, Any] = {
    "id": 38367,
    "odoo_id": "4977",
    "name": "Sara Nabil",
    "full_name": "Sara Nabil Fouad",
    "email": "sara.nabil@example.test",
    "mobile": "+201009876543",
    "employee_code": "10538",
    "status": "active",
    "department": {"id": 214413, "odoo_id": "77", "name": "Sales"},
    "company": {"id": 1, "odoo_id": "1", "name": "The Address Investments"},
    "position": {"id": 60, "name": "Senior Property Consultant"},
    "sector": "Finance ",
    "job_level_name": None,
    "job_level_grade": None,
}


def program(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": 718,
        "title": "Negotiation Essentials",
        "subtitle": "Closing with confidence",
        "description": "Two half-days on discovery and objection handling.",
        "status": "upcoming",
        "computed_status": "upcoming",
        "capacity": 20,
        "type": "internal",
        "target": "public",
        "start_date": "2026-09-10",
        "end_date": "2026-09-11",
        "created_at": "2026-09-02T16:01:23+03:00",
        "updated_at": "2026-09-02T16:01:23+03:00",
        "parent": None,
        "track": {
            "id": 17,
            "title": "Sales Excellence",
            "subtitle": "Core track",
            "description": "Foundational sales skills.",
            "status": "upcoming",
        },
        "departments": [],
        "companies": [],
        "statistics": {
            "capacity": 20,
            "sessions_count": 2,
            "enrolled_users_count": 2,
            "attended_users_count": 1,
            "enrolled_attended_users_count": 1,
            "attendance_records_count": 1,
            "attendance_rate": 50,
            "survey_respondents_count": 1,
            "survey_answers_count": 1,
            "assessment_respondents_count": 1,
            "assessment_answers_count": 1,
        },
        "sessions": [
            {
                "id": 543,
                "session_date": "2026-09-10",
                "session_time_from": "09:00:00",
                "session_time_to": "13:00:00",
                "trainer_name": "Mona Saeed",
                "location": {"id": 15, "name": "Training Room A"},
                "attendance_count": 1,
                "attendance": [
                    {
                        "id": 1167,
                        "user_odoo_id": "4821",
                        "user": USER_AHMED,
                        "attended_at": "2026-09-02T16:01:23+03:00",
                    }
                ],
            },
            {
                "id": 544,
                "session_date": "2026-09-11",
                "session_time_from": "09:00:00",
                "session_time_to": "13:00:00",
                "trainer_name": "Mona Saeed",
                "location": {"id": 15, "name": "Training Room A"},
                "attendance_count": 0,
                "attendance": [],
            },
        ],
        "users": [
            {
                "user_odoo_id": "4821",
                "user": USER_AHMED,
                "is_enrolled": True,
                "enrolled_at": "2026-09-02T16:01:23+03:00",
                "attendance_rate": 50,
                "survey_answers": [
                    {
                        "id": 282,
                        "question_id": 42,
                        "question_title": "How useful was the program?",
                        "answer_type": "select",
                        "answer": "Very useful",
                        "selected_option": {"id": 35, "value": "Very useful"},
                        "answered_at": "2026-09-02T16:01:23+03:00",
                    }
                ],
                "assessment_answers": [
                    {
                        "id": 282,
                        "question_id": 42,
                        "question_title": "Describe the BATNA of your last deal.",
                        "answer": "We walked away and re-anchored a week later.",
                        "answered_at": "2026-09-02T16:01:23+03:00",
                    }
                ],
            },
            {
                "user_odoo_id": "4977",
                "user": USER_SARA,
                "is_enrolled": True,
                "enrolled_at": "2026-09-02T16:01:23+03:00",
                "attendance_rate": 0,
                "survey_answers": [],
                "assessment_answers": [],
            },
        ],
        "survey": {
            "id": 43,
            "title": "Post-program feedback",
            "status": "active",
            "questions": [
                {
                    "id": 42,
                    "title": "How useful was the program?",
                    "answer_type": "select",
                    "required": True,
                    "options": [{"id": 35, "value": "Very useful", "description": None}],
                }
            ],
        },
        "assessment": {
            "id": 42,
            "name": "Negotiation knowledge check",
            "questions": [
                {"id": 42, "title": "Describe the BATNA of your last deal.", "is_required": True}
            ],
        },
    }
    payload.update(overrides)
    return payload


def page(programs: list[dict[str, Any]] | None = None, **meta: Any) -> dict[str, Any]:
    entries = [program()] if programs is None else programs
    block = {
        "current_page": 1,
        "per_page": 25,
        "total": len(entries),
        "last_page": 1,
        "from": 1 if entries else None,
        "to": len(entries) or None,
        "has_more_pages": False,
    }
    block.update(meta)
    return {"programs": entries, "meta": block}
