"""Conforming categoricals on the way in.

Every conformer is total: a value or a reason, never a guess. These tests exist
mostly to pin that there is no third outcome, because a conformer that quietly
returned "Unknown" would put a category nobody chose into the dimension and
remove any chance of noticing the source had changed.
"""

from __future__ import annotations

import pytest

from lnd.models.app_ import QuarantineReason
from lnd.transform.conform import conform_grade, conform_optional_text, conform_sector


class TestOptionalText:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Finance ", "Finance"),
            (" Finance", "Finance"),
            ("Real  Estate", "Real Estate"),
            ("Real\tEstate", "Real Estate"),
            ("", None),
            ("   ", None),
            (None, None),
        ],
    )
    def test_it_trims_collapses_and_treats_empty_as_absent(
        self, raw: object, expected: str | None
    ) -> None:
        assert conform_optional_text(raw) == expected

    def test_a_non_breaking_space_conforms_to_an_ordinary_one(self) -> None:
        """NFKC first, so the damage that is invisible everywhere is caught too.

        A trailing space at least shows up if you look for it. A trailing
        U+00A0 renders identically to nothing at all in a spreadsheet, a psql
        result and a JSON dump, compares unequal in all three, and survives a
        naive `.strip()` untouched.
        """
        assert conform_optional_text("Finance\u00a0") == "Finance"

    def test_two_spellings_of_one_sector_become_one_value(self) -> None:
        """P-05, stated as the thing that actually matters. 940 of 1,052 rows
        arrived with a trailing space, turning 23 sectors into 28."""
        assert conform_optional_text("Finance ") == conform_optional_text("Finance")


class TestSector:
    def test_absent_is_not_a_problem(self) -> None:
        """Not every employee record carries a sector, and a null is a fact
        about the person rather than a fault in the payload."""
        value, problem = conform_sector(None)
        assert (value, problem) == (None, None)

    def test_it_conforms_rather_than_rejecting_whitespace(self) -> None:
        value, problem = conform_sector("  Finance  ")
        assert value == "Finance"
        assert problem is None

    def test_something_far_too_long_is_a_payload_change_not_a_sector(self) -> None:
        value, problem = conform_sector("x" * 200)
        assert value is None
        assert problem is not None
        assert problem.reason is QuarantineReason.INVALID_VALUE


class TestGrade:
    def test_the_live_form_is_a_bare_number(self) -> None:
        assert conform_grade("9") == (9, None)

    def test_the_documented_form_is_g_prefixed(self) -> None:
        """The API document describes `"G7"`; the live payload sends `"9"`.
        Accepting both costs one line and means the transform does not break on
        the day the CRM starts sending what its own document promises."""
        assert conform_grade("G7") == (7, None)

    def test_ten_is_greater_than_nine_once_it_is_an_integer(self) -> None:
        """The whole point of casting. As text, `"10"` sorts before `"9"`, which
        is how a seniority ordering comes to be silently upside down."""
        ten, _ = conform_grade("10")
        nine, _ = conform_grade("9")
        assert ten is not None and nine is not None
        assert ten > nine

    def test_zero_is_a_freelancer_not_an_error(self) -> None:
        """Measured, not assumed: all 20 employees carrying grade 0 have
        `job_level_name = "Freelancer"`, and every Freelancer carries it. They
        are outside the ladder rather than at the bottom of it.

        Rejecting them — which this did first — would have removed 20 active
        employees from the participation denominator. A quarantine is not a free
        action when the row is a person who has to be counted.
        """
        assert conform_grade("0") == (None, None)

    def test_a_negative_grade_is_rejected(self) -> None:
        value, problem = conform_grade("-3")
        assert value is None
        assert problem is not None
        assert problem.reason is QuarantineReason.INVALID_VALUE

    def test_something_unparseable_says_so_rather_than_defaulting(self) -> None:
        value, problem = conform_grade("senior")
        assert value is None
        assert problem is not None
        assert "senior" in problem.detail

    def test_absent_is_not_a_problem(self) -> None:
        assert conform_grade(None) == (None, None)
        assert conform_grade("") == (None, None)
