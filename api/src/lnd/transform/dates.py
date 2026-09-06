"""`core.dim_date`: the calendar, generated rather than synced.

A date dimension earns its place here for one reason that has nothing to do
with convenience: **a month with no training must still appear in a trend, as a
zero.** Group the facts themselves by month and an empty month simply is not in
the result set, so the chart draws a line from August to October and the gap
reads as a dip rather than as an absence. Every monthly figure in section 9 is
rendered as a trend (FR-D04), so this affects all of them.

It also gives every view one spelling of "September 2026" — the dashboard, the
XLSX export and the PDF pack all read `month_label` rather than each formatting
a date in whatever locale the process happens to have.

FISCAL YEAR

Calendar-aligned, because nobody has told us otherwise. That is a *stated
assumption*, not a discovered fact, and it is held in its own column precisely
so that correcting it is a regeneration of this table and a change to nothing
else. If L&D's year starts in July, set `FISCAL_YEAR_START_MONTH` to 7, re-run
`ensure_dates`, and every fiscal rollup follows. See the week-3 open questions.
"""

from __future__ import annotations

import calendar
import logging
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from lnd.models.core import DimDate

log = logging.getLogger(__name__)

#: 1 = the fiscal year starts in January. An assumption, held in one place.
FISCAL_YEAR_START_MONTH = 1

#: How far past the latest known fact date to pre-generate. A fact whose date
#: has no `dim_date` row fails its foreign key, and the failure would land on
#: whichever unlucky pass first sees a session scheduled for next year. A year
#: of headroom is a few hundred rows.
LOOKAHEAD_DAYS = 400


def _fiscal(day: date) -> tuple[int, int]:
    """(fiscal_year, fiscal_quarter) for a date.

    The fiscal year is named for the calendar year it *starts* in — the
    convention has to be picked and written down, because a platform that
    labels FY differently from the finance team's spreadsheets is worse than
    one with no fiscal columns at all.
    """
    offset = (day.month - FISCAL_YEAR_START_MONTH) % 12
    fiscal_year = day.year if day.month >= FISCAL_YEAR_START_MONTH else day.year - 1
    return fiscal_year, offset // 3 + 1


def build_row(day: date) -> dict[str, object]:
    """One `dim_date` row. Pure, so the test asserts on values rather than SQL."""
    fiscal_year, fiscal_quarter = _fiscal(day)
    return {
        "date_key": day,
        "year": day.year,
        "quarter": (day.month - 1) // 3 + 1,
        "month": day.month,
        "month_label": f"{calendar.month_name[day.month]} {day.year}",
        "month_start": day.replace(day=1),
        "day_of_month": day.day,
        # ISO: Monday is 1, Sunday is 7. `weekday()` is 0-based, so add one.
        "day_of_week": day.isoweekday(),
        "is_weekend": day.isoweekday() >= 6,
        "fiscal_year": fiscal_year,
        "fiscal_quarter": fiscal_quarter,
    }


def ensure_dates(session: Session, *, start: date, end: date) -> int:
    """Fill `dim_date` across [start, end]. Returns how many rows were added.

    Idempotent by conflict, not by a read-then-write check: two transform
    passes running at once would both see the same gap and both try to fill it,
    and `ON CONFLICT DO NOTHING` makes the loser a no-op instead of an
    IntegrityError that fails an otherwise good pass.
    """
    if end < start:
        raise ValueError(f"date range is inverted: {start} to {end}")

    rows = []
    day = start
    while day <= end:
        rows.append(build_row(day))
        day += timedelta(days=1)

    if not rows:
        return 0

    statement = insert(DimDate).values(rows).on_conflict_do_nothing(index_elements=["date_key"])
    added = len(session.execute(statement.returning(DimDate.date_key)).scalars().all())

    if added:
        log.info(
            "extended the calendar",
            extra={
                "event": "transform.dates.extended",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "added": added,
            },
        )
    return added


def ensure_covering(session: Session, days: list[date]) -> int:
    """Ensure every date in `days` has a row, plus a year of headroom.

    Called by the transform before any fact is written, with every date the
    pass is about to reference. Filling the *span* rather than the individual
    dates is deliberate: the empty months between two clusters of training are
    exactly the rows a trend needs and a per-date fill would omit.
    """
    if not days:
        return 0

    earliest = min(days)
    latest = max(days)

    # Extend from the earliest date the platform has ever seen, so that
    # backfilling an older period later does not leave a hole behind the
    # existing rows.
    known_earliest = session.scalar(select(func.min(DimDate.date_key)))
    if known_earliest is not None and known_earliest < earliest:
        earliest = known_earliest

    return ensure_dates(
        session, start=earliest.replace(day=1), end=latest + timedelta(days=LOOKAHEAD_DAYS)
    )
