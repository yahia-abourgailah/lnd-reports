"""Rendering an `ExportTable` as CSV or XLSX.

Two writers over one table, so the two formats of one export cannot disagree
about a figure. Both put the stamp in the file rather than beside it: an export
that needed a companion document to be readable is an export that will be read
without one.

WHERE THE STAMP GOES, AND THE TRADE IT MAKES

In CSV, above the header, each line prefixed with `#`. A reader opening it in
Excel sees the scope before the numbers, which is the point; a naive parser
pointed at row 1 sees the stamp instead of the header, which is the cost. The
alternative — stamp at the bottom — puts the scope where a reader scrolling a
1,000-row extract never reaches it, and that is the failure this whole module
exists to prevent. `skip_stamp` is available for a machine-to-machine feed.

In XLSX the stamp is its own sheet, always first, so the data sheet opens clean
and the scope is one tab away rather than pushed into the rows.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from lnd.export.tables import Cell, ExportTable

#: Prefix marking a stamp line in CSV. Chosen because every spreadsheet
#: application shows it and every `#`-aware parser can skip it.
COMMENT = "#"

HEADER_FILL = PatternFill("solid", fgColor="EAEEF0")
LABEL_FONT = Font(bold=True)
TITLE_FONT = Font(bold=True, size=14)


def _plain(value: Cell) -> str | int | float | bool | None:
    """A value a writer can hold without losing what it is.

    `Decimal` goes to `float` for XLSX because openpyxl cannot store one, and a
    string would arrive as text in a column somebody wants to sum. The precision
    lost is irrelevant at these magnitudes and the alternative — a spreadsheet
    of numbers that will not add up — is not.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def to_csv(table: ExportTable, *, skip_stamp: bool = False) -> str:
    """The table as CSV text, stamp first unless asked otherwise."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")

    if not skip_stamp:
        for label, value in table.stamp.as_rows():
            writer.writerow([f"{COMMENT} {label}", value])
        writer.writerow([])

    writer.writerow(list(table.columns))
    for row in table.rows:
        writer.writerow(["" if cell is None else _plain(cell) for cell in row])
    return buffer.getvalue()


def _write_stamp_sheet(workbook: Workbook, table: ExportTable) -> None:
    sheet: Worksheet = workbook.create_sheet("Provenance", 0)
    sheet["A1"] = "What this file is"
    sheet["A1"].font = TITLE_FONT

    for index, (label, value) in enumerate(table.stamp.as_rows(), start=3):
        sheet.cell(row=index, column=1, value=label).font = LABEL_FONT
        cell = sheet.cell(row=index, column=2, value=value)
        cell.alignment = Alignment(wrap_text=True, vertical="top")

    sheet.column_dimensions["A"].width = 34
    sheet.column_dimensions["B"].width = 110


def write_table_sheet(sheet: Worksheet, table: ExportTable) -> None:
    """The header row and the data, with widths that make it readable."""
    sheet["A1"] = table.title
    sheet["A1"].font = TITLE_FONT

    for column, name in enumerate(table.columns, start=1):
        cell = sheet.cell(row=3, column=column, value=name.replace("_", " "))
        cell.font = LABEL_FONT
        cell.fill = HEADER_FILL

    for row_index, row in enumerate(table.rows, start=4):
        for column, value in enumerate(row, start=1):
            sheet.cell(row=row_index, column=column, value=_plain(value))

    for column, name in enumerate(table.columns, start=1):
        longest = max(
            [len(name)] + [len(str(row[column - 1] or "")) for row in table.rows] or [len(name)]
        )
        sheet.column_dimensions[get_column_letter(column)].width = min(max(longest + 2, 12), 80)

    # The header stays visible while somebody scrolls a thousand rows. Cheap,
    # and the difference between a readable extract and a wall of values.
    sheet.freeze_panes = "A4"


def to_xlsx(table: ExportTable) -> bytes:
    """The table as a workbook: provenance first, then the data."""
    workbook = Workbook()
    data = workbook.active
    assert data is not None
    data.title = table.name[:31] or "data"
    write_table_sheet(data, table)
    _write_stamp_sheet(workbook, table)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


__all__ = ["COMMENT", "to_csv", "to_xlsx", "write_table_sheet"]
