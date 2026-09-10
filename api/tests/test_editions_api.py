"""Generating the monthly report keeps an edition — the route's own obligation.

`test_retention` proves the store behaves. This proves the route uses it. A file
handed to somebody and not recorded is the exact case retention exists to
prevent, and it is the kind of omission that surfaces months later as "we cannot
find what we sent".

WHY THIS IS NOT IN `test_export.py`

That module holds `core_db` open for its whole run — an uncommitted transaction
that has TRUNCATEd the star schema and therefore holds ACCESS EXCLUSIVE on it.
These tests go through the API, which opens its own connection, and reading
`core` from a second connection would block on that lock until the test suite
timed out. `live_db` and `core_db` cannot be used together, and the file
boundary is what keeps that from being rediscovered by whoever adds the next
route test.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("live_db")


def _sign_in(client: TestClient) -> None:
    client.get("/v1/auth/login", follow_redirects=True)


class TestGeneratingTheMonthlyReportKeepsAnEdition:
    def test_the_report_route_records_what_it_returned(self, dev_bypass_client: TestClient) -> None:
        _sign_in(dev_bypass_client)
        response = dev_bypass_client.get("/v1/exports/monthly.pdf?year=2026&month=2")
        assert response.status_code == 200

        editions = dev_bypass_client.get("/v1/exports/editions").json()["editions"]
        february = [row for row in editions if row["period"] == "2026-02"]
        assert february, "generating the report kept no edition"
        assert february[0]["generated_by"], "a manual generation has an author"

        stored = dev_bypass_client.get(f"/v1/exports/editions/{february[0]['id']}")
        assert stored.content == response.content, "the edition is not the file that was sent"

    def test_a_regeneration_that_changed_nothing_adds_no_row(
        self, dev_bypass_client: TestClient
    ) -> None:
        _sign_in(dev_bypass_client)
        for _ in range(3):
            dev_bypass_client.get("/v1/exports/monthly.pdf?year=2026&month=3")

        editions = dev_bypass_client.get("/v1/exports/editions").json()["editions"]
        march = [
            row for row in editions if row["period"] == "2026-03" and row["kind"] == "monthly_pdf"
        ]
        assert len(march) == 1, "three identical generations should be one edition"

    def test_the_month_is_published_in_one_format(self, dev_bypass_client: TestClient) -> None:
        """There was a workbook route beside this one, and publishing both put
        two files of the same numbers in one mail and two rows per month on the
        Reports screen, leaving every recipient to decide which was the report.

        `export.monthly` still builds that grid — the parallel run compares it
        cell against cell with the sheet this replaces — but building it for a
        comparison and handing it to somebody as the month are different acts.
        """
        _sign_in(dev_bypass_client)
        assert (
            dev_bypass_client.get("/v1/exports/monthly.xlsx?year=2026&month=4").status_code == 404
        )

        dev_bypass_client.get("/v1/exports/monthly.pdf?year=2026&month=4")
        editions = dev_bypass_client.get("/v1/exports/editions").json()["editions"]
        april = {row["kind"] for row in editions if row["period"] == "2026-04"}
        assert april == {"monthly_pdf"}

    def test_the_listing_never_carries_a_file(self, dev_bypass_client: TestClient) -> None:
        _sign_in(dev_bypass_client)
        dev_bypass_client.get("/v1/exports/monthly.pdf?year=2026&month=5")
        body = dev_bypass_client.get("/v1/exports/editions").json()
        assert body["editions"]
        assert all("content" not in row for row in body["editions"])

    def test_an_unknown_edition_is_a_404(self, dev_bypass_client: TestClient) -> None:
        _sign_in(dev_bypass_client)
        assert dev_bypass_client.get("/v1/exports/editions/999999").status_code == 404

    def test_year_and_month_go_together(self, dev_bypass_client: TestClient) -> None:
        _sign_in(dev_bypass_client)
        assert dev_bypass_client.get("/v1/exports/monthly.pdf?year=2026").status_code == 422

    def test_signing_in_is_required(self, client: TestClient) -> None:
        assert client.get("/v1/exports/editions").status_code == 401


class TestThePdfRoutes:
    def test_the_figures_pack_is_served_as_a_pdf(self, dev_bypass_client: TestClient) -> None:
        _sign_in(dev_bypass_client)
        response = dev_bypass_client.get("/v1/exports/kpis.pdf")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.content.startswith(b"%PDF-")

    def test_the_pdf_route_is_reachable_past_the_templated_one(
        self, dev_bypass_client: TestClient
    ) -> None:
        """`/kpis.{fmt}` matches `kpis.pdf` too, and the first route declared
        wins. Declared in the wrong order this 422s on a format the module
        serves — which is a routing accident, not a decision."""
        _sign_in(dev_bypass_client)
        assert dev_bypass_client.get("/v1/exports/kpis.pdf").status_code == 200
        assert dev_bypass_client.get("/v1/exports/kpis.csv").status_code == 200
        assert dev_bypass_client.get("/v1/exports/kpis.docx").status_code == 422

    def test_an_unknown_programme_is_a_404(self, dev_bypass_client: TestClient) -> None:
        _sign_in(dev_bypass_client)
        assert dev_bypass_client.get("/v1/exports/programs/999999/scorecard.pdf").status_code == 404
