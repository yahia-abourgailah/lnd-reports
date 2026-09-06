"""Populating `core.dim_date`.

Generated, not transformed — the calendar owes nothing to any source. It is
built once over a range wide enough to cover the data and refreshed only when
the range needs extending, which is why this is a function somebody calls rather
than a step in the nightly run.
"""

from __future__ import annotations

import calendar
import datetime as dt

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from lnd.config import get_settings
from lnd.models.core import DimDate

#: Friday and Saturday, not Saturday and Sunday. The company and its sessions
#: are in Egypt, so the working week runs Sunday to Thursday. Hardcoding the
#: Western weekend would misreport "sessions delivered at the weekend" on every
#: row, and — worse — it would look plausible.
WEEKEND_ISO_DAYS = frozenset({5, 6})  # 1 = Monday ... 7 = Sunday


def fiscal_year_and_quarter(day: dt.date, start_month: int) -> tuple[int, int]:
    """The fiscal year and quarter a day falls in.

    A fiscal year is labelled by the calendar year it **ends** in, which is the
    common convention: with an April start, 2026-04-01 is in FY2027.

    Finance confirmed a January start, so today both values equal their civil
    counterparts and this function is an identity. The offset arithmetic is
    kept and tested anyway — an unexercised branch discovered on the day the
    answer changes is worse than one that has been correct all along.
    """
    if start_month == 1:
        return day.year, (day.month - 1) // 3 + 1

    months_in = (day.month - start_month) % 12
    fiscal_year = day.year + 1 if day.month >= start_month else day.year
    return fiscal_year, months_in // 3 + 1


def _row(day: dt.date, start_month: int) -> dict[str, object]:
    fiscal_year, fiscal_quarter = fiscal_year_and_quarter(day, start_month)
    return {
        "date_key": day.year * 10000 + day.month * 100 + day.day,
        "day": day,
        "year": day.year,
        "quarter": (day.month - 1) // 3 + 1,
        "month": day.month,
        "day_of_month": day.day,
        "day_of_week": day.isoweekday(),
        "week_of_year": day.isocalendar().week,
        "month_name": calendar.month_name[day.month],
        "month_abbr": calendar.month_abbr[day.month],
        "year_month": f"{day.year:04d}-{day.month:02d}",
        "fiscal_year": fiscal_year,
        "fiscal_quarter": fiscal_quarter,
        "is_weekend": day.isoweekday() in WEEKEND_ISO_DAYS,
    }


def populate(session: Session, *, start: dt.date, end: dt.date) -> int:
    """Fill the calendar from `start` to `end` inclusive. Idempotent.

    `ON CONFLICT DO NOTHING` rather than an upsert: a day's attributes are a
    function of the day itself, so an existing row is already correct and
    rewriting it would only churn. The one attribute that could change is the
    fiscal offset, and that is a deliberate rebuild — see `rebuild_fiscal`.
    """
    if end < start:
        raise ValueError(f"end {end} precedes start {start}")

    start_month = get_settings().fiscal_year_start_month
    rows = [
        _row(start + dt.timedelta(days=offset), start_month)
        for offset in range((end - start).days + 1)
    ]

    # RETURNING rather than rowcount: a multi-row INSERT ... ON CONFLICT DO
    # NOTHING reports -1 through psycopg, so the count has to come from the
    # rows the statement actually wrote.
    statement = (
        insert(DimDate)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["date_key"])
        .returning(DimDate.date_key)
    )
    return len(session.execute(statement).all())


def rebuild_fiscal(session: Session) -> int:
    """Restate every existing row's fiscal columns from the current setting.

    Separate from `populate`, and deliberately not automatic. Changing the
    fiscal year start silently rewrites what "this quarter" meant in every
    report ever run, so it should be an act somebody performs and can point at,
    not a side effect of the next nightly job.
    """
    start_month = get_settings().fiscal_year_start_month
    days = session.scalars(select(DimDate.day)).all()

    updated = 0
    for day in days:
        fiscal_year, fiscal_quarter = fiscal_year_and_quarter(day, start_month)
        result = session.execute(
            update(DimDate)
            .where(DimDate.day == day)
            .where(
                or_(
                    DimDate.fiscal_year != fiscal_year,
                    DimDate.fiscal_quarter != fiscal_quarter,
                )
            )
            .values(fiscal_year=fiscal_year, fiscal_quarter=fiscal_quarter)
        )
        updated += result.rowcount  # type: ignore[attr-defined]
    return updated
