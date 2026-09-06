"""`core.dim_date`.

Generated rather than transformed, so the tests are about the calendar being
right — and about the fiscal offset, which is the only part a person can get
wrong in a way that silently restates every report.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lnd.config import get_settings
from lnd.models.core import DimDate
from lnd.transform import dates


@pytest.fixture
def calendar(db: Session) -> Session:
    from lnd.db import Base

    Base.metadata.create_all(db.get_bind(), tables=[DimDate.__table__], checkfirst=True)
    db.execute(DimDate.__table__.delete())
    return db


class TestFiscalArithmetic:
    def test_a_january_start_is_the_calendar_year(self) -> None:
        """The company's actual calendar, confirmed by Finance on 2026-09-06."""
        assert dates.fiscal_year_and_quarter(dt.date(2026, 3, 31), 1) == (2026, 1)
        assert dates.fiscal_year_and_quarter(dt.date(2026, 12, 31), 1) == (2026, 4)

    def test_a_fiscal_year_is_named_for_the_year_it_ends_in(self) -> None:
        """With an April start, April 2026 opens FY2027. This is the convention
        Finance will expect, and getting it backwards shifts every annual total
        by a year while remaining perfectly self-consistent."""
        assert dates.fiscal_year_and_quarter(dt.date(2026, 4, 1), 4) == (2027, 1)
        assert dates.fiscal_year_and_quarter(dt.date(2026, 3, 31), 4) == (2026, 4)

    def test_the_quarter_wraps_with_the_offset(self) -> None:
        assert dates.fiscal_year_and_quarter(dt.date(2026, 7, 1), 4) == (2027, 2)
        assert dates.fiscal_year_and_quarter(dt.date(2026, 1, 1), 7) == (2026, 3)


class TestPopulate:
    def test_it_fills_the_range_inclusive(self, calendar: Session) -> None:
        written = dates.populate(calendar, start=dt.date(2026, 1, 1), end=dt.date(2026, 1, 31))

        assert written == 31
        assert calendar.scalar(select(func.count()).select_from(DimDate)) == 31

    def test_running_it_twice_writes_nothing_the_second_time(self, calendar: Session) -> None:
        """Extending the calendar must be safe to repeat — it is called when a
        range runs out, and nobody should have to check first."""
        dates.populate(calendar, start=dt.date(2026, 1, 1), end=dt.date(2026, 1, 31))
        again = dates.populate(calendar, start=dt.date(2026, 1, 1), end=dt.date(2026, 2, 28))

        assert again == 28  # February only; January was already there
        assert calendar.scalar(select(func.count()).select_from(DimDate)) == 59

    def test_the_key_encodes_the_day(self, calendar: Session) -> None:
        """A check constraint enforces it in the database as well, so a row that
        disagreed could not be written even by hand."""
        dates.populate(calendar, start=dt.date(2026, 2, 14), end=dt.date(2026, 2, 14))

        row = calendar.scalars(select(DimDate)).one()
        assert row.date_key == 20260214
        assert row.year_month == "2026-02"

    def test_the_weekend_is_friday_and_saturday(self, calendar: Session) -> None:
        """Egypt, not the United States. A hardcoded Sat/Sun would misreport
        "sessions delivered at the weekend" on every row and look right."""
        dates.populate(calendar, start=dt.date(2026, 2, 2), end=dt.date(2026, 2, 8))

        weekend = {
            row.day.isoformat()
            for row in calendar.scalars(select(DimDate).where(DimDate.is_weekend)).all()
        }
        assert weekend == {"2026-02-06", "2026-02-07"}  # Friday and Saturday

    def test_an_inverted_range_is_refused(self, calendar: Session) -> None:
        with pytest.raises(ValueError, match="precedes"):
            dates.populate(calendar, start=dt.date(2026, 2, 1), end=dt.date(2026, 1, 1))


class TestRebuildFiscal:
    def test_changing_the_start_month_is_deliberate_not_automatic(
        self, calendar: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Populating again after the setting changes leaves existing rows
        alone, because a day's attributes are a function of the day. Restating
        what "this quarter" meant in every report ever run has to be an act
        somebody performs and can point at."""
        dates.populate(calendar, start=dt.date(2026, 4, 1), end=dt.date(2026, 4, 1))

        monkeypatch.setenv("FISCAL_YEAR_START_MONTH", "4")
        get_settings.cache_clear()

        dates.populate(calendar, start=dt.date(2026, 4, 1), end=dt.date(2026, 4, 1))
        assert calendar.scalars(select(DimDate)).one().fiscal_year == 2026

        assert dates.rebuild_fiscal(calendar) == 1
        assert calendar.scalars(select(DimDate)).one().fiscal_year == 2027

    def test_rebuilding_with_no_change_touches_nothing(self, calendar: Session) -> None:
        dates.populate(calendar, start=dt.date(2026, 1, 1), end=dt.date(2026, 1, 10))

        assert dates.rebuild_fiscal(calendar) == 0
