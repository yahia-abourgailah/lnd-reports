"""Downloading a view as a file.

Every route here honours the same filters the screen did — they are the same
`FilterParams` the dashboard uses — and every file carries the stamp. The
formats differ; the figures cannot, because both writers render one table built
from the registry.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from lnd.api.v1.kpis import FilterParams
from lnd.auth.dependencies import CurrentUser
from lnd.db import get_db
from lnd.export import monthly as monthly_report
from lnd.export import tables, writers
from lnd.metrics import registry
from lnd.metrics.filters import MetricFilters, UnsupportedFilter

router = APIRouter(prefix="/exports", tags=["exports"])

DbSession = Annotated[Session, Depends(get_db)]

Format = Literal["csv", "xlsx"]

CSV_TYPE = "text/csv; charset=utf-8"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _stamped_name(stem: str, extension: str) -> str:
    """A filename carrying the date it was produced.

    Two exports of the same view a month apart must not share a name. They hold
    different numbers, and a download folder with one `kpis.xlsx` in it is a
    download folder where the older one silently won.
    """
    return f"lnd-{stem}-{dt.date.today().isoformat()}.{extension}"


def _render(table: tables.ExportTable, fmt: Format) -> Response:
    if fmt == "csv":
        return Response(
            content=writers.to_csv(table),
            media_type=CSV_TYPE,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{_stamped_name(table.name, "csv")}"'
                )
            },
        )
    return Response(
        content=writers.to_xlsx(table),
        media_type=XLSX_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{_stamped_name(table.name, "xlsx")}"'
        },
    )


@router.get("/kpis.{fmt}")
def kpis(
    fmt: Format, _user: CurrentUser, session: DbSession, filters: FilterParams
) -> Response:
    """Every figure the filters allow, with its definition beside it."""
    return _render(tables.kpi_table(session, filters), fmt)


@router.get("/records/{key}.{fmt}")
def records(
    key: str, fmt: Format, _user: CurrentUser, session: DbSession, filters: FilterParams
) -> Response:
    """The rows behind one metric — the drill-through, as a file."""
    try:
        return _render(tables.records_table(session, key, filters), fmt)
    except registry.UnknownMetric as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except UnsupportedFilter as exc:
        # The same refusal the metric gives. A file of rows the number was not
        # computed over is a worse lie than no file, and it is one that lasts.
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/monthly.xlsx")
def monthly(
    _user: CurrentUser,
    session: DbSession,
    filters: FilterParams,
    year: Annotated[int | None, Query(ge=2020, le=2100)] = None,
    month: Annotated[int | None, Query(ge=1, le=12)] = None,
) -> Response:
    """The formatted monthly report, in the workbook's DASHBOARD layout.

    `year` and `month` name the period and override the filter bar's dates,
    because a report titled August must mean August however it was reached.
    Without them the last complete month is used — what the scheduled job will
    ask for — and any other filters on the request still apply.
    """
    if (year is None) != (month is None):
        raise HTTPException(
            status_code=422, detail="year and month are given together or not at all"
        )
    if year is None or month is None:
        year, month = monthly_report.previous_month()

    window = monthly_report.month_window(year, month)
    period_filters = MetricFilters(
        **{**vars(filters), "date_from": window.date_from, "date_to": window.date_to}
    )

    content = monthly_report.monthly_report(session, period_filters)
    return Response(
        content=content,
        media_type=XLSX_TYPE,
        headers={
            "Content-Disposition": (
                f'attachment; filename="lnd-monthly-report-{year:04d}-{month:02d}.xlsx"'
            )
        },
    )
