"""Saved views.

Two things here matter more than the CRUD. The first is that a stored query
string is still a filter set the API accepts — a bookmark that 422s a month
later, in front of the person who saved it, is worse than no bookmark. The
second is drift: the parser's key set and `_filters`' parameters are two lists
of the same thing, and a filter added to one and not the other would be silently
dropped from every view saved afterwards. That is asserted rather than
remembered.
"""

from __future__ import annotations

import datetime as dt
import inspect

import pytest
from fastapi.testclient import TestClient

from lnd.api.v1 import kpis, views

pytestmark = pytest.mark.usefixtures("live_db")


class TestTheParserAndTheApiCannotDrift:
    def test_every_filter_the_api_reads_can_be_saved(self) -> None:
        """The guard against the quiet failure.

        A dimension added to `_filters` and forgotten here would not error. It
        would be dropped from every view saved from that day, and the views
        would keep opening — narrower than the person meant, with nothing
        saying so.
        """
        api_keys = {
            name
            for name in inspect.signature(kpis._filters).parameters
            if name not in {"date_from", "date_to"}
        }
        assert api_keys == set(views.LIST_KEYS), (
            "the saved-view parser and the API filter parameters disagree"
        )

    def test_the_date_parameters_are_handled_too(self) -> None:
        api_dates = {
            name for name in inspect.signature(kpis._filters).parameters if name.startswith("date_")
        }
        assert api_dates == set(views.DATE_KEYS)


class TestParsing:
    def test_a_query_becomes_the_filters_it_describes(self) -> None:
        filters, _ = views.parse_query("?sector=Finance&sector=Sales&date_from=2026-01-01")
        assert filters.sectors == frozenset({"Finance", "Sales"})
        assert filters.date_from == dt.date(2026, 1, 1)

    def test_an_unknown_parameter_is_refused(self) -> None:
        """Refused at save time, where it can still be fixed.

        Stored and refused later, it fails on the screen of whoever opened the
        view — who did not write it and cannot tell what went wrong.
        """
        with pytest.raises(views.InvalidQuery):
            views.parse_query("?sekter=Finance")

    def test_a_non_numeric_id_is_refused(self) -> None:
        with pytest.raises(views.InvalidQuery):
            views.parse_query("?program=not-a-number")

    def test_a_broken_date_is_refused(self) -> None:
        with pytest.raises(views.InvalidQuery):
            views.parse_query("?date_from=last-tuesday")

    def test_the_stored_query_is_canonical(self) -> None:
        """Rebuilt from what was understood, not kept verbatim.

        Two saves of one view must produce one string, so duplicates and
        parameter order cannot make identical views look different.
        """
        _, one = views.parse_query("?sector=Sales&sector=Finance&sector=Sales")
        _, other = views.parse_query("?sector=Finance&sector=Sales")
        assert one == other

    def test_values_are_encoded(self) -> None:
        """`company=The MarQ Communities` written raw comes back as three
        parameters the next time anything parses it."""
        filters, query = views.parse_query("?company=The+MarQ+Communities")
        assert " " not in query
        again, _ = views.parse_query(query)
        assert again.companies == filters.companies

    def test_a_views_own_parameter_survives(self) -> None:
        """`by` is not a filter, and clearing it would forget which dimension a
        breakdown was broken down by — the thing the view was saved for."""
        _, query = views.parse_query("?company=MarQ&by=department")
        assert "by=department" in query


class TestTheRoutes:
    def _save(self, client: TestClient, **body: object) -> object:
        return client.post("/v1/views", json={"path": "/coverage", "query": "", **body})

    def test_saving_and_listing(self, dev_bypass_client: TestClient) -> None:
        dev_bypass_client.get("/v1/auth/login", follow_redirects=True)
        created = self._save(
            dev_bypass_client, name="MarQ Q3", query="?company=MarQ&date_from=2026-07-01"
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["mine"] is True
        assert "company in MarQ" in body["describes"]

        listed = dev_bypass_client.get("/v1/views").json()["views"]
        assert [row["name"] for row in listed if row["name"] == "MarQ Q3"]

        dev_bypass_client.delete(f"/v1/views/{body['id']}")

    def test_a_duplicate_name_is_a_conflict(self, dev_bypass_client: TestClient) -> None:
        dev_bypass_client.get("/v1/auth/login", follow_redirects=True)
        first = self._save(dev_bypass_client, name="Twice")
        assert first.status_code == 201
        assert self._save(dev_bypass_client, name="Twice").status_code == 409
        dev_bypass_client.delete(f"/v1/views/{first.json()['id']}")

    def test_a_screen_that_is_not_a_view_is_refused(self, dev_bypass_client: TestClient) -> None:
        """An allowlist, because the stored path is handed to the router.

        "Wherever the client says" is how a stored string becomes an open
        redirect.
        """
        dev_bypass_client.get("/v1/auth/login", follow_redirects=True)
        response = self._save(dev_bypass_client, name="Elsewhere", path="https://evil.example")
        assert response.status_code == 422

    def test_an_unparseable_filter_is_refused(self, dev_bypass_client: TestClient) -> None:
        dev_bypass_client.get("/v1/auth/login", follow_redirects=True)
        response = self._save(dev_bypass_client, name="Broken", query="?sekter=Finance")
        assert response.status_code == 422

    def test_renaming_and_deleting(self, dev_bypass_client: TestClient) -> None:
        dev_bypass_client.get("/v1/auth/login", follow_redirects=True)
        created = self._save(dev_bypass_client, name="Before").json()
        renamed = dev_bypass_client.patch(f"/v1/views/{created['id']}", json={"name": "After"})
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "After"

        assert dev_bypass_client.delete(f"/v1/views/{created['id']}").status_code == 204
        assert dev_bypass_client.get(f"/v1/views/{created['id']}").status_code in (404, 405)

    def test_a_name_is_trimmed(self, dev_bypass_client: TestClient) -> None:
        """P-05's shape, in a place it costs nothing to prevent: "Q3" and
        "Q3 " sorting apart in a list of six."""
        dev_bypass_client.get("/v1/auth/login", follow_redirects=True)
        created = self._save(dev_bypass_client, name="  Trimmed  ").json()
        assert created["name"] == "Trimmed"
        dev_bypass_client.delete(f"/v1/views/{created['id']}")

    def test_signing_in_is_required(self, client: TestClient) -> None:
        assert client.get("/v1/views").status_code == 401
