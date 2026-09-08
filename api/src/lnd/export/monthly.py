"""The monthly report, laid out like the workbook's DASHBOARD tab.

Recipients see the sheet they already know. What changes is that nobody spends
8-12 hours a cycle assembling it, and that every figure on it is the registry's
rather than a pivot's.

WHAT IS REPRODUCED, AND WHAT DELIBERATELY IS NOT

The workbook has thirteen sheets and only DASHBOARD is the target. Attendance
Master Sheet, Head Count 212, Attendance Prep, Calc, Linked In no and the pivot
tabs are the machinery that produced it — they are what the platform *replaces*,
and reproducing them would be reproducing eight hours of manual assembly in
code.

The layout is taken from the file: a title across the top, labels in row 7 and
their values in row 8, participation in column Z, and the public/customised
split in AA/AB. The column positions are the workbook's, so a reader's eye lands
where it always has.

THE ONE CELL THAT MUST NOT BE REPRODUCED FAITHFULLY

L8 held 0.9272… formatted as 92.7%, labelled "NPS Overall Score". NPS is an
index from -100 to +100, and the workbook's figure was a percentage of
something else. Writing +88.2 into the cell that used to show 92.7% invites
precisely the reading the metric layer exists to prevent — a fall in
satisfaction that did not happen.

So that cell carries the unit in its label and the sheet carries the
reconciliation note. This is the one place the export departs from the original
layout, and it departs from it on purpose.
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy.orm import Session

from lnd.export.provenance import RECONCILIATION_NOTE, Stamp, stamp
from lnd.metrics import registry
from lnd.metrics.base import MetricValue, Unit
from lnd.metrics.filters import MetricFilters, UnsupportedFilter

TITLE_FONT = Font(bold=True, size=16)
LABEL_FONT = Font(bold=True, size=10)
VALUE_FONT = Font(bold=True, size=20)
NOTE_FONT = Font(italic=True, size=9)


@dataclass(frozen=True)
class Placement:
    """One figure, in the column the workbook put it in."""

    metric_key: str
    label: str
    #: 1-indexed column for both the label (row 7) and the value (row 8), as in
    #: the original: B, D, F, H, L, N, Q, T, W.
    column: int


#: Read out of `docs/L&D Main Reports.xlsx`, DASHBOARD, row 7.
PLACEMENTS: tuple[Placement, ...] = (
    Placement("total_programs", "Total Programs", 2),  # B
    Placement("training_days", "Training Days", 4),  # D
    Placement("total_participants", "Total Participants", 6),  # F
    Placement("training_hours_delivered", "Training Hours", 8),  # H
    # The label carries the unit. See the module docstring: this is the cell
    # that would otherwise read as a fall from 92.7%.
    Placement("nps", "NPS Overall Score (index, -100 to +100)", 12),  # L
    Placement("facilitator_performance", "Facilitators Performance", 14),  # N
    Placement("knowledge_relevance", "Knowledge Relevance", 17),  # Q
    Placement("activity_effectiveness", "Activity Effectiveness", 20),  # T
    Placement("logistics_effectiveness", "Logistics Effectiveness", 23),  # W
)

#: Column Z in the original, where the participation rate sat on its own.
PARTICIPATION_COLUMN = 26
#: AA/AB held the public-versus-customised split as a label/value pair.
SPLIT_LABEL_COLUMN = 27
SPLIT_VALUE_COLUMN = 28


def month_window(year: int, month: int) -> MetricFilters:
    """The filters for one calendar month.

    A month is a closed range rather than "the last thirty days", because a
    report titled August must mean August whenever it happens to be generated —
    including when it is regenerated in October to settle a question.
    """
    last = calendar.monthrange(year, month)[1]
    return MetricFilters(date_from=dt.date(year, month, 1), date_to=dt.date(year, month, last))


def previous_month(today: dt.date | None = None) -> tuple[int, int]:
    """The last complete month. What the scheduled report defaults to."""
    now = today or dt.date.today()
    first = now.replace(day=1)
    last_month = first - dt.timedelta(days=1)
    return last_month.year, last_month.month


def _cell_value(value: MetricValue) -> float | int | str | None:
    """The number a spreadsheet should hold.

    Percentages go in as proportions with a percent format applied, so the cell
    behaves like the workbook's did and can be charted. NPS goes in as the index
    it is — formatting it as a percentage is the mistake this whole sheet is
    careful about.
    """
    if value.value is None:
        return None
    if value.unit is Unit.PERCENT:
        return float(value.value) / 100
    return float(value.value)


def _number_format(value: MetricValue) -> str:
    if value.unit is Unit.PERCENT:
        return "0.0%"
    if value.unit in (Unit.HOURS, Unit.MONTHS, Unit.NPS):
        return "0.0"
    return "0"


def _write_figure(sheet: Worksheet, column: int, label: str, value: MetricValue | None) -> None:
    label_cell = sheet.cell(row=7, column=column, value=label)
    label_cell.font = LABEL_FONT
    label_cell.alignment = Alignment(wrap_text=True, vertical="bottom")

    cell = sheet.cell(row=8, column=column)
    if value is None:
        # An em dash, never a zero. Zero is a measurement, and a metric the
        # filters put out of reach was not measured.
        cell.value = "—"
    else:
        cell.value = _cell_value(value)
        cell.number_format = _number_format(value)
    cell.font = VALUE_FONT


def _sample_note(values: dict[str, MetricValue]) -> str:
    """The response count behind the four quality scores and NPS.

    On the sheet rather than in the stamp, because these five figures sit
    together in one strip and a reader comparing them to last month needs to
    know whether the denominator moved. 98% over 297 responses and 98% over 4
    are different claims.
    """
    nps = values.get("nps")
    if nps is None:
        return ""
    return (
        f"Quality scores and NPS are computed over {nps.sample_size} survey "
        "responses in this period, every one of them in scope."
    )


def _dashboard(sheet: Worksheet, session: Session, filters: MetricFilters) -> Stamp:
    computed: dict[str, MetricValue] = {}
    for key in [p.metric_key for p in PLACEMENTS] + [
        "participation_rate",
        "public_program_share",
    ]:
        try:
            computed[key] = registry.compute(key, session, filters)
        except UnsupportedFilter:
            continue

    sheet["A1"] = "Learning and Development Dashboard"
    sheet["A1"].font = TITLE_FONT
    sheet.merge_cells(start_row=1, start_column=1, end_row=2, end_column=25)

    period = filters.describe()
    sheet["A4"] = f"Period: {period}"
    sheet["A4"].font = LABEL_FONT

    for placement in PLACEMENTS:
        _write_figure(sheet, placement.column, placement.label, computed.get(placement.metric_key))

    _write_figure(
        sheet,
        PARTICIPATION_COLUMN,
        "Participation rate",
        computed.get("participation_rate"),
    )

    # AA/AB in the original: two labels, two values, stacked. The workbook's
    # pair did not sum to 100%; these do, because they are one share and its
    # complement computed from one population.
    public = computed.get("public_program_share")
    sheet.cell(row=7, column=SPLIT_LABEL_COLUMN, value="Public Calendar").font = LABEL_FONT
    sheet.cell(row=8, column=SPLIT_LABEL_COLUMN, value="Customised").font = LABEL_FONT
    if public is not None and public.value is not None:
        share = float(public.value) / 100
        for row, number in ((7, share), (8, 1 - share)):
            cell = sheet.cell(row=row, column=SPLIT_VALUE_COLUMN, value=number)
            cell.number_format = "0.0%"

    note_row = 11
    for text in (_sample_note(computed), RECONCILIATION_NOTE):
        if not text:
            continue
        cell = sheet.cell(row=note_row, column=1, value=text)
        cell.font = NOTE_FONT
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        sheet.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=25)
        note_row += 2

    for placement in PLACEMENTS:
        sheet.column_dimensions[get_column_letter(placement.column)].width = 18
    sheet.row_dimensions[7].height = 30

    return stamp(session, filters, list(computed.values()))


def monthly_report(session: Session, filters: MetricFilters) -> bytes:
    """The formatted monthly XLSX: the DASHBOARD layout, and its provenance."""
    from lnd.export.tables import ExportTable, kpi_table
    from lnd.export.writers import write_table_sheet

    workbook = Workbook()
    dashboard = workbook.active
    assert dashboard is not None
    dashboard.title = "DASHBOARD"
    file_stamp = _dashboard(dashboard, session, filters)

    # Every figure, not only the nine on the strip. The workbook's DASHBOARD was
    # the whole report because assembling more was manual; here the detail costs
    # nothing and answers the next question in the same file.
    detail: ExportTable = kpi_table(session, filters)
    write_table_sheet(workbook.create_sheet("All figures"), detail)

    provenance = workbook.create_sheet("Provenance")
    provenance["A1"] = "What this file is"
    provenance["A1"].font = TITLE_FONT
    for index, (label, value) in enumerate(file_stamp.as_rows(), start=3):
        provenance.cell(row=index, column=1, value=label).font = LABEL_FONT
        provenance.cell(row=index, column=2, value=value).alignment = Alignment(
            wrap_text=True, vertical="top"
        )
    provenance.column_dimensions["A"].width = 34
    provenance.column_dimensions["B"].width = 110

    import io

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


__all__ = [
    "PARTICIPATION_COLUMN",
    "PLACEMENTS",
    "month_window",
    "monthly_report",
    "previous_month",
]
