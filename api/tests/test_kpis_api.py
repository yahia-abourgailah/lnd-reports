"""The dashboard's read endpoints.

These go through HTTP rather than calling the metric layer directly, because
what week 5 adds is the envelope and the filter parsing — the arithmetic is
already pinned by `test_metrics.py` and `test_reference.py`. What can break
here is a filter that does not reach the metric, an envelope field that stops
being sent, or a refusal that turns into a plausible wrong answer.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from lnd.ingest.landing import land
from lnd.ingest.models import Entity, Source
from lnd.transform.runner import transform_programs
from tests.test_metrics import program

#: `app.survey_question_map` is deliberately absent. Migration 0009 seeds it and
#: several other tests read those rows straight from the migrated database, so a
#: fixture that truncated it would leave every later test measuring a platform
#: whose survey answers no longer score — silently, because an empty map does
#: not error. It is also exactly the mapping this fixture wants, so there is
#: nothing to insert either.
TRUNCATE = (
    "TRUNCATE raw.source_record, core.dim_date, core.dim_trainer, "
    "core.dim_employee, core.dim_program, core.dim_session, "
    "core.fact_enrollment, core.fact_attendance, core.fact_evaluation, "
    "app.enrichment_program_override, app.trainer_alias, "
    "app.survey_option_score, app.identity_mapping, ops.dq_exception "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture
def dashboard(live_db: None, dev_bypass_client: TestClient) -> Iterator[TestClient]:
    """A signed-in client over a database holding one transformed program.

    `live_db` rather than `core_db`: a request opens its own session, so the
    rolled-back transaction the transform tests use would be invisible to it.
    The data has to be committed, which means this fixture is responsible for
    clearing up after itself — hence the truncate at both ends rather than only
    the start.
    """
    from lnd.db import session_scope

    with session_scope() as session:
        session.execute(text(TRUNCATE))

    with session_scope() as session:
        land(session, source=Source.CRM, entity=Entity.PROGRAM, records=[("93", program())])
        transform_programs(session)
        # Salma attended and has since left. Only this flag keeps her out of a
        # denominator she is not in — see test_metrics.py.
        session.execute(
            text("UPDATE core.dim_employee SET on_current_roster = false WHERE odoo_id = '4003'")
        )

    dev_bypass_client.get("/v1/auth/login")
    yield dev_bypass_client

    with session_scope() as session:
        session.execute(text(TRUNCATE))


def body(client: TestClient, url: str) -> dict[str, Any]:
    response = client.get(url)
    assert response.status_code == 200, response.text
    return dict(response.json())


class TestTheEnvelope:
    def test_every_response_says_how_old_the_data_is(self, dashboard: TestClient) -> None:
        """A figure with no age is a figure nobody can tell is stale, and the
        platform serves last-known-good on purpose when a source is down."""
        assert "freshness" in body(dashboard, "/v1/kpis")

    def test_every_response_says_what_it_was_computed_over(self, dashboard: TestClient) -> None:
        """P-03 was a filter applied up a spreadsheet and inherited by
        everything below it, with nothing on screen to say so."""
        unfiltered = body(dashboard, "/v1/kpis")
        filtered = body(dashboard, "/v1/kpis?sector=Commercial")

        assert "full population" in unfiltered["filters_applied"]
        assert unfiltered["dimensions_filtered"] == []
        assert "Commercial" in filtered["filters_applied"]
        assert filtered["dimensions_filtered"] == ["sector"]

    def test_every_response_says_how_many_rows_it_could_not_place(
        self, dashboard: TestClient
    ) -> None:
        """A dashboard that silently omits rows is the workbook."""
        assert body(dashboard, "/v1/kpis")["excluded_count"] >= 0

    def test_a_metric_ships_with_its_definition_and_population(self, dashboard: TestClient) -> None:
        """The tooltip reads these. A number and its definition ship together
        or the definition is not enforceable."""
        metric = next(
            m for m in body(dashboard, "/v1/kpis")["metrics"] if m["key"] == "participation_rate"
        )

        assert metric["definition"]
        assert metric["population"]
        assert metric["excludes"]
        assert metric["provenance"] == "corrected"
        assert metric["note"]

    def test_a_ratio_returns_both_terms(self, dashboard: TestClient) -> None:
        """So a breakdown re-aggregates by summing them rather than averaging
        quotients."""
        metric = next(
            m for m in body(dashboard, "/v1/kpis")["metrics"] if m["key"] == "participation_rate"
        )

        assert metric["numerator"] is not None
        assert metric["denominator"] is not None


class TestFilters:
    def test_a_filter_reaches_the_metric(self, dashboard: TestClient) -> None:
        narrowed = body(dashboard, "/v1/kpis?sector=Nowhere")
        participants = next(m for m in narrowed["metrics"] if m["key"] == "total_participants")

        assert float(participants["value"]) == 0

    def test_repeated_parameters_are_one_filter(self, dashboard: TestClient) -> None:
        """`?sector=A&sector=B` is what a filter bar produces and what a URL can
        hold, so a filtered view is shareable as a link."""
        response = dashboard.get("/v1/kpis?sector=Commercial&sector=Nowhere")

        assert response.status_code == 200
        assert "Commercial" in response.json()["filters_applied"]

    def test_a_metric_that_refuses_the_filters_is_absent_not_an_error(
        self, dashboard: TestClient
    ) -> None:
        """A trainer dashboard shows what means something for a trainer.
        Participation Rate is not among them, and its absence is the honest
        answer to a question that has none."""
        keys = {m["key"] for m in body(dashboard, "/v1/kpis?trainer=1")["metrics"]}

        assert "participation_rate" not in keys
        assert "training_days" in keys

    def test_no_filters_returns_every_metric(self, dashboard: TestClient) -> None:
        assert len(body(dashboard, "/v1/kpis")["metrics"]) == 21


class TestBreakdown:
    def test_the_parts_sum_to_the_whole(self, dashboard: TestClient) -> None:
        """The property the design exists for. Each slice is the same metric
        with a narrower filter, so summing both ratio terms reproduces the
        overall figure — which a grouped second implementation would only do
        until somebody corrected one and not the other.
        """
        result = body(dashboard, "/v1/kpis/total_participants/breakdown?by=company")
        parts = sum(float(s["metric"]["value"]) for s in result["slices"])

        assert parts == float(result["overall"]["value"])

    def test_it_returns_the_overall_figure_too(self, dashboard: TestClient) -> None:
        """So a breakdown that does not add up is visible rather than inferred."""
        assert body(dashboard, "/v1/kpis/nps/breakdown?by=company")["overall"]["key"] == "nps"

    def test_a_dimension_the_metric_has_no_population_for_is_refused(
        self, dashboard: TestClient
    ) -> None:
        """422, not a number. A trainer breakdown of Participation Rate narrows
        the numerator and leaves the denominator whole — the arithmetic behind
        the published 60.4% (P-13)."""
        response = dashboard.get("/v1/kpis/participation_rate/breakdown?by=trainer")

        assert response.status_code == 422
        assert "trainer" in response.json()["detail"]

    def test_period_is_a_trend_not_a_breakdown(self, dashboard: TestClient) -> None:
        response = dashboard.get("/v1/kpis/nps/breakdown?by=period")

        assert response.status_code == 422

    def test_an_unknown_metric_is_a_404(self, dashboard: TestClient) -> None:
        assert dashboard.get("/v1/kpis/not_a_metric/breakdown?by=company").status_code == 404


class TestTrend:
    def test_each_point_is_that_month_alone(self, dashboard: TestClient) -> None:
        """Not a running total. A cumulative line rises forever and says
        nothing about whether March was better than February."""
        points = body(dashboard, "/v1/kpis/total_participants/trend")["points"]
        with_data = [p for p in points if float(p["metric"]["value"] or 0) > 0]

        assert len(with_data) == 1
        assert with_data[0]["key"] == "2026-02"

    def test_points_are_labelled_for_a_chart_axis(self, dashboard: TestClient) -> None:
        points = body(dashboard, "/v1/kpis/nps/trend")["points"]

        assert points[0]["label"].startswith(
            ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
        )

    def test_an_unknown_metric_is_a_404(self, dashboard: TestClient) -> None:
        assert dashboard.get("/v1/kpis/not_a_metric/trend").status_code == 404


class TestDimensions:
    def test_it_offers_only_values_that_exist(self, dashboard: TestClient) -> None:
        """Filtering to a department nobody is in returns an empty dashboard
        that looks like a bug."""
        options = {d["dimension"]: d for d in body(dashboard, "/v1/kpis/dimensions")["dimensions"]}
        companies = {v["label"] for v in options["company"]["values"]}

        assert companies == {"The Address Investments"}

    def test_values_carry_their_row_counts(self, dashboard: TestClient) -> None:
        """So the bar can say "Sales (312)" and the shape of the answer is
        visible before anybody asks for it."""
        options = {d["dimension"]: d for d in body(dashboard, "/v1/kpis/dimensions")["dimensions"]}

        assert all(v["count"] >= 1 for v in options["sector"]["values"])
        assert options["sector"]["counts"] == "employees"

    def test_programs_are_keyed_on_id_never_title(self, dashboard: TestClient) -> None:
        """Two programs have shared a title and been merged by a report that
        grouped on it (P-02). A bar that sent titles would reintroduce that."""
        options = {d["dimension"]: d for d in body(dashboard, "/v1/kpis/dimensions")["dimensions"]}
        program_values = options["program"]["values"]

        assert program_values[0]["value"] == "93"
        assert program_values[0]["label"] == "The Adaptive Leader"

    def test_period_is_not_offered_as_a_list(self, dashboard: TestClient) -> None:
        """A date range is not a list of values to pick from, and every month
        as a checkbox is a worse date picker than a date picker."""
        dimensions = {d["dimension"] for d in body(dashboard, "/v1/kpis/dimensions")["dimensions"]}

        assert "period" not in dimensions


class TestAuthentication:
    def test_every_route_requires_a_session(self, client: TestClient) -> None:
        """These describe internal systems and the people in them, unlike
        /v1/health which a load balancer must reach before anyone signs in."""
        for url in (
            "/v1/kpis",
            "/v1/kpis/dimensions",
            "/v1/kpis/nps/trend",
            "/v1/kpis/nps/breakdown?by=company",
        ):
            assert client.get(url).status_code == 401, url


class TestDrillThrough:
    """`/v1/drill/{key}` — the rows behind a number.

    The guarantee is that `total` equals the metric's own sample. A
    drill-through with its own idea of the population would agree the day it
    was written and drift silently the first time a population rule was
    corrected, because both numbers stay plausible.
    """

    def test_the_row_count_matches_the_metric(self, dashboard: TestClient) -> None:
        for key in ("total_programs", "nps", "participation_rate", "total_participants"):
            drilled = body(dashboard, f"/v1/drill/{key}")
            assert drilled["total"] == drilled["metric"]["sample_size"], key

    def test_it_returns_the_metric_beside_the_rows(self, dashboard: TestClient) -> None:
        """So a mismatch between a figure and its rows is visible on one screen
        rather than across two requests."""
        drilled = body(dashboard, "/v1/drill/total_programs")

        assert drilled["metric"]["key"] == "total_programs"
        assert drilled["rows"][0]["title"] == "The Adaptive Leader"

    def test_the_grain_is_the_metric_s_own(self, dashboard: TestClient) -> None:
        assert body(dashboard, "/v1/drill/participation_rate")["grain"] == "employee"
        assert body(dashboard, "/v1/drill/nps")["grain"] == "evaluation"

    def test_a_truncated_list_says_so(self, dashboard: TestClient) -> None:
        """A truncated list that looks complete is worse than no list."""
        drilled = body(dashboard, "/v1/drill/total_participants?limit=1")

        assert drilled["truncated"] is True
        assert drilled["returned"] == 1
        assert drilled["total"] > 1

    def test_filters_narrow_the_rows_and_the_metric_together(self, dashboard: TestClient) -> None:
        drilled = body(dashboard, "/v1/drill/total_participants?sector=Nowhere")

        assert drilled["total"] == 0
        assert drilled["rows"] == []

    def test_it_refuses_what_the_metric_refuses(self, dashboard: TestClient) -> None:
        """Showing rows the number was not computed over is a worse lie than
        showing none."""
        assert dashboard.get("/v1/drill/participation_rate?trainer=1").status_code == 422

    def test_an_unknown_metric_is_a_404(self, dashboard: TestClient) -> None:
        assert dashboard.get("/v1/drill/not_a_metric").status_code == 404

    def test_it_requires_a_session(self, client: TestClient) -> None:
        assert client.get("/v1/drill/nps").status_code == 401


class TestEnrichmentApi:
    def test_the_author_is_the_signed_in_user_not_the_request_body(
        self, dashboard: TestClient
    ) -> None:
        """An audit trail a caller can address to somebody else is not an audit
        trail."""
        response = dashboard.put(
            "/v1/enrichment/program_override",
            json={
                "key": {"crm_program_id": 93, "field": "trainer_name"},
                "values": {"value": "Ahmed Elshiaty"},
                "authored_by": "someone.else@example.com",
            },
        )

        assert response.status_code == 200
        assert response.json()["authored_by"] == "specialist@example.com"

    def test_a_change_is_visible_as_history(self, dashboard: TestClient) -> None:
        key = {"crm_program_id": 93, "field": "trainer_name"}
        for value in ("first", "second"):
            dashboard.put(
                "/v1/enrichment/program_override",
                json={"key": key, "values": {"value": value}},
            )

        entries = dashboard.post(
            "/v1/enrichment/program_override/history", json={"key": key}
        ).json()["entries"]

        assert [(e["values"]["value"], e["is_live"]) for e in entries] == [
            ("first", False),
            ("second", True),
        ]

    def test_a_write_is_committed(self, dashboard: TestClient) -> None:
        """`get_db` commits nothing — read paths must not — so a write route
        that forgot would return 200 and change nothing, which is the worst
        possible combination."""
        key = {"crm_program_id": 93, "field": "trainer_name"}
        dashboard.put(
            "/v1/enrichment/program_override", json={"key": key, "values": {"value": "kept"}}
        )

        live_rows = body(dashboard, "/v1/enrichment/program_override")["entries"]
        assert [e["values"]["value"] for e in live_rows] == ["kept"]

    def test_retiring_leaves_the_history(self, dashboard: TestClient) -> None:
        key = {"crm_program_id": 93, "field": "trainer_name"}
        dashboard.put(
            "/v1/enrichment/program_override", json={"key": key, "values": {"value": "gone"}}
        )
        dashboard.post(
            "/v1/enrichment/program_override/retire",
            json={"key": key, "note": "the CRM fixed it"},
        )

        assert body(dashboard, "/v1/enrichment/program_override")["entries"] == []
        past = dashboard.post("/v1/enrichment/program_override/history", json={"key": key}).json()[
            "entries"
        ]
        assert len(past) == 1
        assert "the CRM fixed it" in past[0]["note"]

    def test_a_partial_write_is_refused(self, dashboard: TestClient) -> None:
        response = dashboard.put(
            "/v1/enrichment/survey_question",
            json={
                "key": {"crm_survey_id": 2, "crm_question_id": 3},
                "values": {"scale_max": 7},
            },
        )

        assert response.status_code == 422

    def test_it_requires_a_session(self, client: TestClient) -> None:
        assert client.get("/v1/enrichment/program_override").status_code == 401
