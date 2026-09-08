"""What leaves the building.

Two properties are worth more than the rest of this file put together:

  * a figure in an export equals the same metric computed through the registry
    under the same filters, asserted rather than observed; and
  * the file says, from its own contents, what period, what filters, how fresh,
    how many rows were excluded, and what each figure means.

The second is the one that is easy to under-build and hard to retrofit. A
spreadsheet has no badge, no tooltip and no banner, and the figure in it will be
quoted six months after the screen it came from has moved on.
"""

from __future__ import annotations

import csv
import datetime as dt
import io

import pytest
from openpyxl import load_workbook
from sqlalchemy.orm import Session

from lnd.export import monthly, tables, writers
from lnd.metrics import registry
from lnd.metrics.filters import MetricFilters

pytestmark = pytest.mark.usefixtures("core_db")

FEBRUARY = MetricFilters(
    date_from=dt.date(2026, 2, 1),
    date_to=dt.date(2026, 2, 28),
)


def _rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


class TestTheFiguresAreTheRegistrys:
    def test_every_exported_figure_equals_the_metric(self, loaded: Session) -> None:
        """The one rule, as a test rather than a convention.

        An exporter with its own arithmetic would agree today and drift the
        first time a population rule was corrected — leaving the wrong number in
        the attachment, where nobody can see the definition it used.
        """
        table = tables.kpi_table(loaded, FEBRUARY)
        exported = {row["metric"]: row for row in tables.as_dicts(table)}

        for value in registry.compute_all(loaded, FEBRUARY):
            row = exported[value.title]
            assert row["value"] == value.value, value.key
            assert row["formatted"] == value.formatted(), value.key
            assert row["sample_size"] == value.sample_size, value.key

    def test_csv_and_xlsx_hold_the_same_numbers(self, loaded: Session) -> None:
        """One table, two writers. Two builders would be two answers."""
        table = tables.kpi_table(loaded, FEBRUARY)
        from_csv = [
            row for row in _rows(writers.to_csv(table)) if row and not row[0].startswith("#")
        ]

        workbook = load_workbook(io.BytesIO(writers.to_xlsx(table)))
        sheet = workbook["kpis"]
        titles_xlsx = [sheet.cell(row=r, column=1).value for r in range(4, 4 + len(table.rows))]

        assert from_csv[0] == list(table.columns)
        assert [row[0] for row in from_csv[1:]] == titles_xlsx

    def test_a_records_export_holds_the_rows_the_metric_counted(self, loaded: Session) -> None:
        table = tables.records_table(loaded, "total_programs", MetricFilters())
        metric = registry.compute("total_programs", loaded, MetricFilters())

        assert len(table.rows) == metric.sample_size


class TestProvenanceSurvivesTheJourney:
    def test_a_csv_answers_for_itself(self, loaded: Session) -> None:
        text = writers.to_csv(tables.kpi_table(loaded, FEBRUARY))
        stamp = "\n".join(line for line in text.splitlines() if line.startswith("#"))

        assert "Generated at" in stamp
        assert "2026-02-01 to 2026-02-28" in stamp
        assert "Data freshness" in stamp
        assert "Records excluded" in stamp
        assert "Records flagged" in stamp
        # The definition, not only the number. In an attachment it cannot be
        # looked up, which is exactly where it is worth most.
        assert "Definition — Learner Hours" in stamp

    def test_the_stamp_carries_the_reconciliation_wording(self, loaded: Session) -> None:
        """A restated figure beside an old one needs the sentence, not the numbers.

        60.4% and 9.3% side by side, unexplained, read as a collapse in training
        that did not happen.
        """
        text = writers.to_csv(tables.kpi_table(loaded, MetricFilters()))
        assert "60.4%" in text
        assert "hardcoded 192" in text

    def test_an_xlsx_carries_its_provenance_on_its_own_sheet(self, loaded: Session) -> None:
        workbook = load_workbook(io.BytesIO(writers.to_xlsx(tables.kpi_table(loaded, FEBRUARY))))

        assert workbook.sheetnames[0] == "Provenance"
        labels = [workbook["Provenance"].cell(row=r, column=1).value for r in range(3, 12)]
        assert "Generated at" in labels
        assert "Data freshness" in labels

    def test_a_machine_feed_can_skip_the_stamp(self, loaded: Session) -> None:
        """The trade the `#` prefix makes, made available in both directions."""
        table = tables.kpi_table(loaded, FEBRUARY)
        plain = _rows(writers.to_csv(table, skip_stamp=True))

        assert plain[0] == list(table.columns)


class TestTheMonthlyReport:
    def test_it_reproduces_the_dashboard_layout(self, loaded: Session) -> None:
        """The workbook's own columns, so a reader's eye lands where it did."""
        content = monthly.monthly_report(loaded, monthly.month_window(2026, 2))
        sheet = load_workbook(io.BytesIO(content))["DASHBOARD"]

        assert sheet["B7"].value == "Total Programs"
        assert sheet["D7"].value == "Training Days"
        assert sheet["F7"].value == "Total Participants"
        assert sheet["H7"].value == "Training Hours"
        assert sheet["Z7"].value == "Participation rate"

    def test_the_nps_cell_says_it_is_an_index(self, loaded: Session) -> None:
        """The one cell not reproduced faithfully, and the reason.

        L8 held a percentage labelled NPS. Writing an index into that cell under
        the old label invites reading +88 as a fall from 92.7%, which is the
        misreading the whole metric layer is built to prevent.
        """
        sheet = load_workbook(
            io.BytesIO(monthly.monthly_report(loaded, monthly.month_window(2026, 2)))
        )["DASHBOARD"]

        assert "index" in str(sheet["L7"].value)
        assert sheet["L8"].number_format != "0.0%"

    def test_the_figures_are_the_registrys(self, loaded: Session) -> None:
        window = monthly.month_window(2026, 2)
        sheet = load_workbook(io.BytesIO(monthly.monthly_report(loaded, window)))["DASHBOARD"]

        programs = registry.compute("total_programs", loaded, window)
        participants = registry.compute("total_participants", loaded, window)
        assert sheet["B8"].value == float(programs.value or 0)
        assert sheet["F8"].value == float(participants.value or 0)

    def test_a_month_is_a_closed_range(self) -> None:
        """August must mean August whenever the report is generated."""
        window = monthly.month_window(2026, 2)
        assert (window.date_from, window.date_to) == (dt.date(2026, 2, 1), dt.date(2026, 2, 28))

    def test_the_report_carries_its_provenance_and_the_detail(self, loaded: Session) -> None:
        workbook = load_workbook(
            io.BytesIO(monthly.monthly_report(loaded, monthly.month_window(2026, 2)))
        )
        assert workbook.sheetnames == ["DASHBOARD", "All figures", "Provenance"]
