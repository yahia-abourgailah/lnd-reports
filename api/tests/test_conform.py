"""Conformance, and the two workbook defects it exists to prevent."""

from __future__ import annotations

import pytest

from lnd.transform.conform import attribute_hash, normalise, trim

#: A real U+00A0, built from an escape rather than typed. A literal one would
#: look identical to a space in a diff and would be "tidied" away by the first
#: person to touch the line — taking the test's whole subject with it.
NBSP = "\u00a0"


class TestTrim:
    def test_collapses_internal_whitespace_as_well_as_stripping(self) -> None:
        assert trim("  Ahmed   Kamal  ") == "Ahmed Kamal"

    def test_empty_after_stripping_is_absent_not_empty(self) -> None:
        """A `""` sector and a `None` sector mean the same thing.

        Stored differently, the coverage view grows an empty-string sector that
        nothing can be done about.
        """
        assert trim("   ") is None
        assert trim("") is None
        assert trim(None) is None

    def test_non_breaking_space_is_whitespace(self) -> None:
        """These arrive from copy-paste into the CRM's own admin screens."""
        assert trim(f"Projects{NBSP}") == "Projects"
        assert normalise(f"Projects{NBSP}") == normalise("Projects")


class TestNormalise:
    def test_p05_the_trailing_space_that_split_a_sector(self) -> None:
        """940 of 1,052 live user objects carry one. This is the whole defect."""
        assert normalise("Projects ") == normalise("Projects") == "projects"

    def test_p04_case_variants_of_one_trainer_collapse(self) -> None:
        assert normalise("Ahmed Nasr") == normalise("ahmed  NASR") == "ahmed nasr"

    def test_accents_fold(self) -> None:
        """The accent is a property of one system's keyboard, not of the person."""
        assert normalise("Ahmèd") == "ahmed"

    def test_initials_are_deliberately_not_merged(self) -> None:
        """The line this module refuses to cross.

        Deciding that `A. Nasr` is `Ahmed Nasr` is a judgement. A matcher that
        got it right nine times in ten would silently merge two real people the
        tenth time, and nothing would show that it had. That merge is authored
        by a person in `app.trainer_alias`.
        """
        assert normalise("A. Nasr") != normalise("Ahmed Nasr")


class TestAttributeHash:
    def test_key_order_does_not_change_the_hash(self) -> None:
        """Two dicts carrying identical information hash identically."""
        assert attribute_hash({"a": 1, "b": 2}) == attribute_hash({"b": 2, "a": 1})

    def test_a_changed_value_changes_the_hash(self) -> None:
        assert attribute_hash({"sector": "Sales"}) != attribute_hash({"sector": "Finance"})

    def test_none_and_absent_are_different(self) -> None:
        """They must be: a cleared sector is a change worth a new SCD version."""
        assert attribute_hash({"sector": None}) != attribute_hash({})

    @pytest.mark.parametrize("value", ["", "Projects", None])
    def test_hash_is_stable_across_calls(self, value: str | None) -> None:
        assert attribute_hash({"sector": value}) == attribute_hash({"sector": value})
