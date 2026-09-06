"""Migration 0009 ships the mapping every quality metric depends on.

Its own module, without the `core_db` fixture the other transform tests use.
That fixture truncates `app.survey_question_map` so each test starts from a
known state — which is right for testing the transform and wrong for testing
that the migration seeded anything, since the rows it is looking for would be
the ones just deleted.

The failure this guards is the quiet kind. An environment with this table empty
does not error: it scores every answer null, returns no value for all five
quality metrics, and looks exactly like an environment where nobody has ever
answered a survey.
"""

from __future__ import annotations

from sqlalchemy import Connection, select
from sqlalchemy.orm import Session

from lnd.models.app_ import SurveyQuestionMap
from lnd.models.core import EvaluationDimension

WORKSHOP_EVALUATION = 2


def _live_rows(connection: Connection) -> dict[int, SurveyQuestionMap]:
    """The mapping as a migrated database actually holds it.

    `superseded_at IS NULL` is the live-row predicate the partial unique index
    uses, so this asks the same question the transform does rather than a
    similar one.
    """
    with Session(bind=connection) as session:
        return {
            row.crm_question_id: row
            for row in session.scalars(
                select(SurveyQuestionMap).where(
                    SurveyQuestionMap.crm_survey_id == WORKSHOP_EVALUATION,
                    SurveyQuestionMap.superseded_at.is_(None),
                )
            )
        }


def test_every_scored_question_is_mapped(db_connection: Connection) -> None:
    """Read from the migrated database, so this fails if the seed stops running
    rather than if a constant somewhere is edited."""
    assert set(_live_rows(db_connection)) == {3, 4, 5, 6, 7}


def test_each_question_maps_to_the_dimension_its_wording_asks_about(
    db_connection: Connection,
) -> None:
    """The part the CRM cannot tell you. Nothing in the payload says question 5
    is the logistics metric; it says "How effective were the logistics
    (handouts, room, food, etc.)", and somebody read that and decided."""
    dimensions = {q: row.dimension for q, row in _live_rows(db_connection).items()}

    assert dimensions == {
        3: EvaluationDimension.KNOWLEDGE_RELEVANCE,
        4: EvaluationDimension.ACTIVITY_EFFECTIVENESS,
        5: EvaluationDimension.LOGISTICS_EFFECTIVENESS,
        6: EvaluationDimension.FACILITATOR_PERFORMANCE,
        7: EvaluationDimension.RECOMMEND,
    }


def test_the_recommend_question_is_the_only_wide_scale(db_connection: Connection) -> None:
    """q7 is 0-10, the quality questions are 1-5.

    Banding a 1-5 answer as NPS would make every response a detractor and
    produce a confident -100, which is why the scale is stored rather than
    assumed. Inferred rather than declared: the CRM states no scale anywhere,
    and observed answers separate q7's maximum of 10 from the others' 5.
    """
    scales = {q: (row.scale_min, row.scale_max) for q, row in _live_rows(db_connection).items()}

    assert scales[7] == (0, 10)
    assert {scales[q] for q in (3, 4, 5, 6)} == {(1, 5)}


def test_the_free_text_question_is_deliberately_unmapped(
    db_connection: Connection,
) -> None:
    """q8 is the comment. It has no dimension and no scale, and mapping it would
    mean inventing a rating for a sentence. It reaches reporting through
    `fact_evaluation.comment` instead."""
    mapped = _live_rows(db_connection)

    assert 8 not in mapped
    assert mapped, "the map is empty — the seed did not run"
