"""The enrichment forms, and the type coercion that makes them usable.

The screen used to render one generic form for all five overlays: a box for the
column name, a box for the keys, and a box where you hand-wrote JSON. It worked,
and only for whoever built it. These tests hold the replacement to two promises.

**A form asks for exactly what the write path accepts.** Offering a field
`author()` refuses is a form somebody fills in and cannot submit; accepting a
field no form offers is a value nobody can supply. Checked at import too, but
asserted here so the failure names the kind.

**A dropdown's value reaches the database as the right type.** An HTML `<select>`
produces `"77"`, and before this that reached PostgreSQL as
`operator does not exist: integer = character varying` — a 500 that told the
person filling the form nothing at all.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from lnd.enrichment import forms as enrichment_forms
from lnd.enrichment.service import TABLES, EnrichmentConflict, OverlayKind, author, coerce, live

# Marked per class, not per module. `core_db` holds an uncommitted transaction
# that has TRUNCATEd the star schema, so it holds ACCESS EXCLUSIVE on it; a test
# going through the API opens its own connection and would block on that lock
# until the suite timed out. `core_db` and `live_db` cannot be used together.


@pytest.mark.usefixtures("core_db")
class TestTheFormMatchesTheWritePath:
    def test_every_overlay_has_a_form(self) -> None:
        described = {form.kind for form in enrichment_forms._FORMS}
        assert described == set(TABLES)

    def test_the_fields_are_the_tables(self) -> None:
        """One declaration, not two. A field added to the write path and
        forgotten here is a value nobody can supply through the screen."""
        for form in enrichment_forms._FORMS:
            table = TABLES[form.kind]
            assert {item.name for item in form.key} == set(table.key), form.kind.value
            assert {item.name for item in form.values} == set(table.fields), form.kind.value

    def test_every_field_is_labelled_in_words(self) -> None:
        """The person who needs this is looking at "Programme 77 has no
        trainer". They should not have to know the column is
        `crm_program_id`."""
        for form in enrichment_forms._FORMS:
            for item in (*form.key, *form.values):
                assert item.label.strip(), f"{form.kind.value}.{item.name}"
                assert item.label != item.name, f"{form.kind.value}.{item.name} is a column name"

    def test_selects_have_options(self, loaded: Session) -> None:
        """A dropdown with nothing in it is worse than a text box."""
        for form in enrichment_forms.forms(loaded):
            for item in (*form.key, *form.values):
                if item.input is enrichment_forms.InputKind.SELECT:
                    assert item.options, f"{form.kind.value}.{item.name} offers nothing"

    def test_the_options_come_from_the_data(self, loaded: Session) -> None:
        """Read live, not typed into the front end, where a list goes stale
        silently and the first symptom is a value somebody cannot select."""
        programmes = next(
            item
            for form in enrichment_forms.forms(loaded)
            if form.kind is OverlayKind.PROGRAM_OVERRIDE
            for item in form.key
            if item.name == "crm_program_id"
        )
        from sqlalchemy import select

        from lnd.models.core import DimProgram

        expected = {
            str(program_id)
            for program_id in loaded.scalars(
                select(DimProgram.crm_program_id).where(DimProgram.deleted_at_source.is_(None))
            )
        }
        assert {option.value for option in programmes.options} == expected
        # Labelled by title, because the dropdown is read by somebody who knows
        # the programme by name and arrived holding only its number.
        assert any("·" in option.label for option in programmes.options)


@pytest.mark.usefixtures("core_db")
class TestCoercion:
    def test_a_dropdowns_string_becomes_the_column_type(self) -> None:
        assert coerce(OverlayKind.PROGRAM_OVERRIDE, "crm_program_id", "77") == 77
        assert coerce(OverlayKind.TRAINER_ALIAS, "trainer_key", "3") == 3

    def test_a_value_already_right_is_untouched(self) -> None:
        assert coerce(OverlayKind.PROGRAM_OVERRIDE, "crm_program_id", 77) == 77
        assert coerce(OverlayKind.PROGRAM_OVERRIDE, "value", "Amr Alaa") == "Amr Alaa"

    def test_nonsense_is_refused_with_the_reason(self) -> None:
        """A 422 naming the field and the type, not a 500 naming nothing."""
        with pytest.raises(EnrichmentConflict, match="takes int"):
            coerce(OverlayKind.PROGRAM_OVERRIDE, "crm_program_id", "seventy-seven")

    def test_authoring_with_a_string_id_works(self, loaded: Session) -> None:
        entry = author(
            loaded,
            OverlayKind.PROGRAM_OVERRIDE,
            key={"crm_program_id": "77", "field": "trainer_name"},
            values={"value": "Amr Alaa"},
            authored_by="specialist@example.com",
            note="from the training calendar",
        )
        assert entry.key["crm_program_id"] == 77
        assert [
            row.key["crm_program_id"] for row in live(loaded, OverlayKind.PROGRAM_OVERRIDE)
        ] == [77]


@pytest.mark.usefixtures("live_db")
class TestTheRoute:
    def test_forms_is_not_shadowed_by_the_kind_route(self, dev_bypass_client: TestClient) -> None:
        """`/{kind}` matches `forms` too, and the first route declared wins —
        so declared second this 422s about an unknown overlay kind."""
        dev_bypass_client.get("/v1/auth/login", follow_redirects=True)
        response = dev_bypass_client.get("/v1/enrichment/forms")
        assert response.status_code == 200
        assert {form["kind"] for form in response.json()} == {kind.value for kind in TABLES}

    def test_signing_in_is_required(self, client: TestClient) -> None:
        assert client.get("/v1/enrichment/forms").status_code == 401
