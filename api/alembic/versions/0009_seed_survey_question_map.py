"""Seed app.survey_question_map for the Workshop Evaluation survey.

Revision ID: 0009
Revises: 0008
Created: week 4

Data, not schema, and deliberately so. Without a row here no answer can be
attributed to a metric: the transform raises SURVEY_QUESTION_UNMAPPED rather
than guessing, `fact_evaluation` scores every column null, and all five quality
metrics — the four percentages and NPS — return no value. An environment with
this table empty looks like an environment where nobody has ever answered a
survey.

WHERE EACH COLUMN COMES FROM, BECAUSE THEY ARE NOT THE SAME KIND OF FACT

`crm_question_id` and `question_title` are the CRM's. All 57 programs use survey
id 2, "Workshop Evaluation", with these six stable ids.

`dimension` is a judgement. Nothing in the payload says question 5 is the
logistics metric; it says "How effective were the logistics (handouts, room,
food, etc.)". Somebody read that and decided. That is exactly why this is an
authored row in `app` rather than a constant in code — when the survey changes,
somebody edits a row instead of a developer editing a module.

`scale_min` and `scale_max` are inferred, and this is the weakest link. The CRM
declares no scale anywhere. Observed answers give q3-q6 a maximum of 5 and q7 a
maximum of 10, which separates the recommend question from the quality ones; the
0 rather than 1 for its floor comes from the NPS convention, not from the data,
because nobody has yet scored below 5. It does not affect the banding — promoter
9-10, detractor 0-6 either way — but it is an assumption and it is on the list
to confirm with the CRM team.

Q8 IS ABSENT ON PURPOSE

It is the free-text comment. It has no dimension and no scale, and it is carried
on `fact_evaluation.comment` rather than scored. Mapping it would require
inventing a rating for a sentence.

Superseded by the enrichment screen in week 6, at which point L&D maintains this
themselves. `superseded_at IS NULL` is the live-row predicate, so an edit there
closes these rows rather than deleting them and the history of what a metric
meant survives.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SURVEY_ID = 2
AUTHOR = "seed:0009"

#: (question id, dimension, scale_min, scale_max, title)
MAPPINGS: tuple[tuple[int, str, int, int, str], ...] = (
    (
        3,
        "knowledge_relevance",
        1,
        5,
        "How relevant was the knowledge you gained throughout the training?",
    ),
    (
        4,
        "activity_effectiveness",
        1,
        5,
        "How effective were the learning activities used in this training?",
    ),
    (
        5,
        "logistics_effectiveness",
        1,
        5,
        "How effective were the logistics (handouts, room, food, etc.) used in the training?",
    ),
    (
        6,
        "facilitator_performance",
        1,
        5,
        "How would you rate the facilitator's overall performance?",
    ),
    (
        7,
        "recommend",
        0,
        10,
        "How likely are you to recommend this training to a friend or teammate?",
    ),
)


def upgrade() -> None:
    table = sa.table(
        "survey_question_map",
        sa.column("crm_survey_id", sa.Integer),
        sa.column("crm_question_id", sa.Integer),
        sa.column("question_title", sa.String),
        sa.column("dimension", sa.String),
        sa.column("scale_min", sa.SmallInteger),
        sa.column("scale_max", sa.SmallInteger),
        sa.column("authored_by", sa.String),
        sa.column("note", sa.String),
        schema="app",
    )

    # Idempotent against a database somebody has already seeded by hand — the
    # dev one was, while the metric layer was being written. The live-row index
    # is partial on `superseded_at IS NULL`, so this checks the same condition
    # rather than assuming the table is empty.
    existing = set(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT crm_question_id FROM app.survey_question_map "
                "WHERE crm_survey_id = :survey AND superseded_at IS NULL"
            ),
            {"survey": SURVEY_ID},
        )
        .scalars()
        .all()
    )

    rows = [
        {
            "crm_survey_id": SURVEY_ID,
            "crm_question_id": question_id,
            "question_title": title,
            "dimension": dimension,
            "scale_min": low,
            "scale_max": high,
            "authored_by": AUTHOR,
            "note": (
                "Question and title from the CRM payload. Dimension is a reading of the "
                "question text. Scale inferred from observed answers; the recommend "
                "floor follows the NPS convention rather than the data."
            ),
        }
        for question_id, dimension, low, high, title in MAPPINGS
        if question_id not in existing
    ]
    if rows:
        op.bulk_insert(table, rows)


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM app.survey_question_map "
            "WHERE crm_survey_id = :survey AND authored_by = :author"
        ).bindparams(survey=SURVEY_ID, author=AUTHOR)
    )
