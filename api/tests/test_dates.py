"""`dim_date`: the calendar, and the empty month it exists to preserve."""

from __future__ import annotations

from datetime import date

import pytest

from lnd.transform import dates
from lnd.transform.dates import build_row


class TestBuildRow:
    def test_attributes_of_a_known_day(self) -> None:
        row = build_row(date(2026, 9, 10))
        assert row["year"] == 2026
        assert row["quarter"] == 3
        assert row["month"] == 9
        assert row["month_label"] == "September 2026"
        assert row["month_start"] == date(2026, 9, 1)
        assert row["day_of_week"] == 4  # a Thursday, ISO
        assert row["is_weekend"] is False

    @pytest.mark.parametrize(
        ("day", "iso_weekday", "weekend"),
        [
            (date(2026, 9, 11), 5, False),  # Friday
            (date(2026, 9, 12), 6, True),  # Saturday
            (date(2026, 9, 13), 7, True),  # Sunday
            (date(2026, 9, 14), 1, False),  # Monday
        ],
    )
    def test_iso_weekday_numbering(self, day: date, iso_weekday: int, weekend: bool) -> None:
        """ISO: Monday is 1, Sunday is 7. `weekday()` is 0-based and is not it."""
        row = build_row(day)
        assert row["day_of_week"] == iso_weekday
        assert row["is_weekend"] is weekend

    def test_quarter_boundaries(self) -> None:
        assert build_row(date(2026, 3, 31))["quarter"] == 1
        assert build_row(date(2026, 4, 1))["quarter"] == 2

    def test_fiscal_is_calendar_aligned_by_default(self) -> None:
        """A stated assumption, not a discovered fact — see the week-3 notes."""
        row = build_row(date(2026, 9, 10))
        assert row["fiscal_year"] == 2026
        assert row["fiscal_quarter"] == 3

    def test_a_july_fiscal_year_moves_both_columns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Correcting the fiscal calendar is a regeneration and nothing else.

        The point of holding fiscal year and quarter as their own columns: if
        L&D's year starts in July, this constant changes, the table is
        regenerated, and every fiscal rollup follows without a single view
        being edited.
        """
        monkeypatch.setattr(dates, "FISCAL_YEAR_START_MONTH", 7)

        september = build_row(date(2026, 9, 10))
        assert september["fiscal_year"] == 2026
        assert september["fiscal_quarter"] == 1  # first quarter of FY2026

        march = build_row(date(2026, 3, 10))
        assert march["fiscal_year"] == 2025  # still in the year that began July 2025
        assert march["fiscal_quarter"] == 3


class TestEnsureDates:
    def test_an_inverted_range_is_rejected(self) -> None:
        """Silently swapping the bounds would hide the caller's bug."""
        with pytest.raises(ValueError, match="inverted"):
            dates.ensure_dates(None, start=date(2026, 9, 10), end=date(2026, 9, 1))  # type: ignore[arg-type]
