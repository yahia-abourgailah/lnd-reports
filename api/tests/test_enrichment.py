"""The overlay: human decisions layered over what the sources say.

The property under test throughout is that nothing is ever mutated. A change
supersedes and inserts, so the audit trail (FR-C03, NFR-07) is a fact about the
storage rather than a log somebody has to remember to write — and the failure
mode of a log you must remember to write is that the one change worth auditing
is the one that skipped it.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lnd.enrichment import EnrichmentConflict, OverlayKind, author, history, live, retire
from lnd.models.app_ import EnrichmentField, ProgramOverride

pytestmark = pytest.mark.usefixtures("core_db")

TRAINER = {"crm_program_id": 93, "field": EnrichmentField.TRAINER_NAME}
MONDAY = dt.datetime(2026, 2, 2, 9, 0, tzinfo=dt.UTC)
TUESDAY = MONDAY + dt.timedelta(days=1)


def value_of(session: Session, key: dict[str, object] | None = None) -> str | None:
    rows = live(session, OverlayKind.PROGRAM_OVERRIDE)
    match = [e for e in rows if e.key == (key or TRAINER)]
    return str(match[0].values["value"]) if match else None


class TestAuthoring:
    def test_a_new_override_is_live(self, core_db: Session) -> None:
        author(
            core_db,
            OverlayKind.PROGRAM_OVERRIDE,
            key=TRAINER,
            values={"value": "Ahmed Elshiaty"},
            authored_by="specialist@example.com",
        )

        assert value_of(core_db) == "Ahmed Elshiaty"

    def test_a_change_supersedes_rather_than_updating(self, core_db: Session) -> None:
        """Two rows afterwards, not one amended row. This is the whole design."""
        for name in ("Ahmed Elshiaty", "Ahmed ElShiaty"):
            author(
                core_db,
                OverlayKind.PROGRAM_OVERRIDE,
                key=TRAINER,
                values={"value": name},
                authored_by="specialist@example.com",
            )

        assert core_db.scalar(select(func.count()).select_from(ProgramOverride)) == 2
        assert value_of(core_db) == "Ahmed ElShiaty"

    def test_the_previous_value_is_still_readable(self, core_db: Session) -> None:
        """ "What was this before, and who changed it" survives the person who
        changed it — which for a table whose whole content is judgement calls
        is the point."""
        author(
            core_db,
            OverlayKind.PROGRAM_OVERRIDE,
            key=TRAINER,
            values={"value": "first"},
            authored_by="one@example.com",
            note="why it was set",
        )
        author(
            core_db,
            OverlayKind.PROGRAM_OVERRIDE,
            key=TRAINER,
            values={"value": "second"},
            authored_by="two@example.com",
        )

        past = history(core_db, OverlayKind.PROGRAM_OVERRIDE, TRAINER)
        assert [(e.values["value"], e.authored_by, e.is_live) for e in past] == [
            ("first", "one@example.com", False),
            ("second", "two@example.com", True),
        ]
        assert past[0].note == "why it was set"

    def test_only_one_row_is_ever_live(self, core_db: Session) -> None:
        """Enforced by a partial unique index, not by this code being careful.
        Two live overrides for one key would make "CRM value or override?"
        depend on row order."""
        for name in ("a", "b", "c"):
            author(
                core_db,
                OverlayKind.PROGRAM_OVERRIDE,
                key=TRAINER,
                values={"value": name},
                authored_by="x@example.com",
            )

        assert len(live(core_db, OverlayKind.PROGRAM_OVERRIDE)) == 1

    def test_re_affirming_the_same_value_still_records_a_row(self, core_db: Session) -> None:
        """It looks redundant and is not. Somebody re-checking a decision after
        a source change is a fact worth keeping; suppressing it would leave a
        gap in the history exactly where the review happened."""
        for _ in range(2):
            author(
                core_db,
                OverlayKind.PROGRAM_OVERRIDE,
                key=TRAINER,
                values={"value": "same"},
                authored_by="x@example.com",
            )

        assert len(history(core_db, OverlayKind.PROGRAM_OVERRIDE, TRAINER)) == 2


class TestRetiring:
    def test_retiring_withdraws_the_override(self, core_db: Session) -> None:
        author(
            core_db,
            OverlayKind.PROGRAM_OVERRIDE,
            key=TRAINER,
            values={"value": "override"},
            authored_by="x@example.com",
        )
        retire(core_db, OverlayKind.PROGRAM_OVERRIDE, key=TRAINER, authored_by="x@example.com")

        assert value_of(core_db) is None

    def test_the_row_is_kept_not_deleted(self, core_db: Session) -> None:
        """ "We overrode this for three months and then stopped" is exactly the
        history somebody needs when a figure moves and nobody remembers why."""
        author(
            core_db,
            OverlayKind.PROGRAM_OVERRIDE,
            key=TRAINER,
            values={"value": "override"},
            authored_by="x@example.com",
        )
        retire(core_db, OverlayKind.PROGRAM_OVERRIDE, key=TRAINER, authored_by="x@example.com")

        assert len(history(core_db, OverlayKind.PROGRAM_OVERRIDE, TRAINER)) == 1

    def test_the_reason_for_withdrawing_is_kept_beside_the_reason_for_setting(
        self, core_db: Session
    ) -> None:
        author(
            core_db,
            OverlayKind.PROGRAM_OVERRIDE,
            key=TRAINER,
            values={"value": "override"},
            authored_by="x@example.com",
            note="set because the CRM was wrong",
        )
        retired = retire(
            core_db,
            OverlayKind.PROGRAM_OVERRIDE,
            key=TRAINER,
            authored_by="x@example.com",
            note="CRM fixed it",
        )

        assert retired is not None
        assert "set because the CRM was wrong" in (retired.note or "")
        assert "CRM fixed it" in (retired.note or "")

    def test_retiring_nothing_is_not_an_error(self, core_db: Session) -> None:
        """It is the state the caller asked for."""
        assert (
            retire(core_db, OverlayKind.PROGRAM_OVERRIDE, key=TRAINER, authored_by="x@example.com")
            is None
        )


class TestRefusals:
    def test_a_partial_write_is_refused(self, core_db: Session) -> None:
        """Setting one field of a multi-field row would leave the others at
        whatever the superseded row happened to hold — an override nobody
        authored, attributed to somebody who did not author it."""
        with pytest.raises(EnrichmentConflict, match="needs"):
            author(
                core_db,
                OverlayKind.SURVEY_QUESTION,
                key={"crm_survey_id": 2, "crm_question_id": 3},
                values={"scale_max": 7},
                authored_by="x@example.com",
            )

    def test_an_unknown_field_is_refused(self, core_db: Session) -> None:
        with pytest.raises(EnrichmentConflict, match="no field"):
            author(
                core_db,
                OverlayKind.PROGRAM_OVERRIDE,
                key=TRAINER,
                values={"value": "x", "invented": 1},
                authored_by="x@example.com",
            )

    def test_an_incomplete_key_is_refused(self, core_db: Session) -> None:
        """A lookup missing half its key would match every field of that
        program, and supersede all of them."""
        with pytest.raises(EnrichmentConflict, match="keyed on"):
            history(core_db, OverlayKind.PROGRAM_OVERRIDE, {"crm_program_id": 93})
