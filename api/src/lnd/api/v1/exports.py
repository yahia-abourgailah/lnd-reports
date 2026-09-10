"""Downloading a view as a file, and reopening one that was published.

Every route here honours the same filters the screen did — they are the same
`FilterParams` the dashboard uses — and every file carries the stamp. The
formats differ; the figures cannot, because all three writers render one table
built from the registry.

THE MONTHLY REPORT IS KEPT; NOTHING ELSE IS

Generating the monthly report stores an edition (`lnd.export.retention`),
because regenerating it later does not reproduce it — a report remade in October
over August's window is the current answer for August, not the file that was
sent in September. Ad-hoc downloads are not stored: they are one person's
question, they carry names, and a table of them would be a second copy of the
roster that nobody audits.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from lnd.analysis import scorecards
from lnd.api.v1.kpis import FilterParams
from lnd.auth.dependencies import CurrentUser
from lnd.db import get_db
from lnd.export import monthly as monthly_report
from lnd.export import pdf, retention, tables, writers
from lnd.metrics import registry
from lnd.metrics.filters import MetricFilters, UnsupportedFilter
from lnd.models.ops import ExportKind, ExportTrigger

router = APIRouter(prefix="/exports", tags=["exports"])

DbSession = Annotated[Session, Depends(get_db)]

Format = Literal["csv", "xlsx"]

CSV_TYPE = "text/csv; charset=utf-8"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF_TYPE = "application/pdf"


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


def _pdf(content: bytes, stem: str) -> Response:
    return Response(
        content=content,
        media_type=PDF_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{_stamped_name(stem, "pdf")}"'},
    )


# Declared before `/kpis.{fmt}`: a templated segment matches `kpis.pdf` too,
# and the first route wins — so leaving this second means a 422 on a format
# this module does serve.
@router.get("/kpis.pdf")
def kpis_pdf(_user: CurrentUser, session: DbSession, filters: FilterParams) -> Response:
    """The same figures, as the pack somebody prints or forwards."""
    return _pdf(pdf.kpi_pack(session, filters), "figures")


@router.get("/kpis.{fmt}")
def kpis(fmt: Format, _user: CurrentUser, session: DbSession, filters: FilterParams) -> Response:
    """Every figure the filters allow, with its definition beside it."""
    return _render(tables.kpi_table(session, filters), fmt)


@router.get("/programs/{program_id}/scorecard.pdf")
def program_scorecard_pdf(
    program_id: int, _user: CurrentUser, session: DbSession, filters: FilterParams
) -> Response:
    """One programme's scorecard, with every comment on it, as a PDF."""
    try:
        content = pdf.program_scorecard_pack(session, program_id, filters)
    except scorecards.UnknownProgram as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return _pdf(content, f"programme-{program_id}")


@router.get("/trainers/{trainer_key}/scorecard.pdf")
def trainer_scorecard_pdf(
    trainer_key: int, _user: CurrentUser, session: DbSession, filters: FilterParams
) -> Response:
    """One trainer's scorecard as a PDF, caveat and all."""
    try:
        content = pdf.trainer_scorecard_pack(session, trainer_key, filters)
    except scorecards.UnknownTrainer as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return _pdf(content, f"trainer-{trainer_key}")


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


def _period(year: int | None, month: int | None) -> tuple[int, int]:
    """The month a monthly report covers.

    `year` and `month` name the period and override the filter bar's dates,
    because a report titled August must mean August however it was reached.
    Without them the last complete month is used — what the scheduled job asks
    for — and any other filters on the request still apply.
    """
    if (year is None) != (month is None):
        raise HTTPException(
            status_code=422, detail="year and month are given together or not at all"
        )
    if year is None or month is None:
        return monthly_report.previous_month()
    return year, month


def _month_filters(filters: MetricFilters, year: int, month: int) -> MetricFilters:
    window = monthly_report.month_window(year, month)
    return MetricFilters(
        **{**vars(filters), "date_from": window.date_from, "date_to": window.date_to}
    )


def _publish(
    session: Session,
    *,
    kind: ExportKind,
    year: int,
    month: int,
    filters: MetricFilters,
    content: bytes,
    media_type: str,
    extension: str,
    user_email: str,
) -> Response:
    """Return the report, and keep the edition that was returned.

    The commit is explicit because `get_db` commits nothing — read paths must
    not, and every other route in this module is one. A file handed to somebody
    and not recorded is the case retention exists to prevent, so the write
    happens before the response leaves.

    The digest is over the figures the report carries — `monthly.headline`, the
    same list both formats lay out — so a regeneration that changed nothing is
    recognised as one and the listing keeps a row per real difference.
    """
    filename = f"lnd-monthly-report-{year:04d}-{month:02d}.{extension}"
    retention.keep(
        session,
        kind=kind,
        trigger=ExportTrigger.MANUAL,
        year=year,
        month=month,
        filename=filename,
        content_type=media_type,
        filters_applied=filters.describe(),
        content=content,
        figures_sha256=retention.figures_digest(monthly_report.headline(session, filters).values()),
        generated_by=user_email,
    )
    session.commit()
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/monthly.pdf")
def monthly_pdf(
    user: CurrentUser,
    session: DbSession,
    filters: FilterParams,
    year: Annotated[int | None, Query(ge=2020, le=2100)] = None,
    month: Annotated[int | None, Query(ge=1, le=12)] = None,
) -> Response:
    """The monthly report. The one format it is published in.

    It reads top to bottom, with the reconciliation note where it cannot be
    scrolled past. There was an XLSX beside it reproducing the workbook's grid,
    and publishing both put two files of the same numbers in one mail and two
    rows per month on the reports screen. `export.monthly` still builds that
    grid for the parallel run to compare against the sheet this replaces, which
    is a reconciliation rather than a publication.

    Ad-hoc XLSX is untouched. "Give me these figures as a spreadsheet" is a
    different request from "publish the month", and the first is what a person
    sorting rows actually wants.
    """
    year, month = _period(year, month)
    period_filters = _month_filters(filters, year, month)
    return _publish(
        session,
        kind=ExportKind.MONTHLY_PDF,
        year=year,
        month=month,
        filters=period_filters,
        content=pdf.monthly_pack(session, year, month, period_filters),
        media_type=PDF_TYPE,
        extension="pdf",
        user_email=user.email,
    )


# ----------------------------------------------------------------- retention
class EditionOut(BaseModel):
    """One kept report, described. Never carries the file itself."""

    id: int
    kind: str
    trigger: str
    #: `YYYY-MM`. The month covered, not the month generated.
    period: str
    filename: str
    content_type: str
    filters_applied: str
    generated_at: dt.datetime
    #: Null when the schedule produced it, rather than a service account
    #: standing in for a person.
    generated_by: str | None
    byte_size: int
    #: A digest of the figures, never of the file. Two editions of one period
    #: with this equal contain the same numbers — which is the question a
    #: reader has, and one a digest of the bytes could not answer, because the
    #: generation timestamp is written into every export.
    figures_sha256: str


class EditionsResponse(BaseModel):
    editions: list[EditionOut]
    #: What the whole table weighs. A retention policy nobody can see the size
    #: of is how one becomes a disk-space incident.
    total_editions: int
    total_bytes: int
    retained_per_period: int


@router.get("/editions", response_model=EditionsResponse)
def list_editions(
    _user: CurrentUser,
    session: DbSession,
    kind: Annotated[ExportKind | None, Query(description="one format only")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> EditionsResponse:
    """Every report that was published, newest first."""
    held, weight = retention.stored_bytes(session)
    return EditionsResponse(
        editions=[
            # Built field by field rather than splatted from a dict. An
            # untyped mapping at an API boundary is a contract nothing
            # verifies — which is exactly how `/learners/search` came to send
            # `full_name` to a screen reading `name`.
            EditionOut(
                id=edition.id,
                kind=edition.kind.value,
                trigger=edition.trigger.value,
                period=f"{edition.period_year:04d}-{edition.period_month:02d}",
                filename=edition.filename,
                content_type=edition.content_type,
                filters_applied=edition.filters_applied,
                generated_at=edition.generated_at,
                generated_by=edition.generated_by,
                byte_size=edition.byte_size,
                figures_sha256=edition.figures_sha256,
            )
            for edition in retention.editions(session, kind=kind, limit=limit)
        ],
        total_editions=held,
        total_bytes=weight,
        retained_per_period=retention.RETAIN_PER_PERIOD,
    )


@router.get("/editions/{edition_id}")
def download_edition(edition_id: int, _user: CurrentUser, session: DbSession) -> Response:
    """The file as it was published — not a fresh answer for that month."""
    edition = retention.fetch(session, edition_id)
    if edition is None:
        raise HTTPException(status_code=404, detail="No such edition.")
    return Response(
        content=edition.content,
        media_type=edition.content_type,
        headers={"Content-Disposition": f'attachment; filename="{edition.filename}"'},
    )
