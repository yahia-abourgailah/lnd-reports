"""The raw-inspection API.

Read-only, authenticated, and the ancestor of week 6's drill-through: it answers
"what did the source actually send us?", which is the question week 4 needs when
a restated figure is challenged.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from lnd.ingest import Entity, Source, land
from tests.fixtures import crm_program


@pytest.fixture
def seeded(db: Session, dev_bypass_client: TestClient, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """A signed-in client whose API reads the test transaction."""
    from lnd.db import get_db
    from lnd.main import create_app

    land(
        db,
        source=Source.CRM,
        entity=Entity.PROGRAM,
        records=[
            ("718", crm_program.program()),
            ("91", crm_program.program(id=91, title="Coaching")),
        ],
    )

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app, follow_redirects=False) as client:
        client.get("/v1/auth/login")
        yield client


# ------------------------------------------------------------------- security
def test_every_raw_endpoint_requires_a_session(client: TestClient) -> None:
    """Payloads carry names, emails and mobile numbers."""
    for path in (
        "/v1/raw/summary",
        "/v1/raw/crm/program",
        "/v1/raw/crm/program/718",
        "/v1/raw/crm/program/718/history",
    ):
        assert client.get(path).status_code == 401, path


# -------------------------------------------------------------------- summary
def test_summary_counts_what_landed(seeded: TestClient) -> None:
    body = seeded.get("/v1/raw/summary").json()
    assert body["total_versions"] == 2

    entity = next(e for e in body["entities"] if e["entity"] == "program")
    assert entity["source"] == "crm"
    assert entity["records"] == 2
    assert entity["versions"] == 2


# ----------------------------------------------------------------------- list
def test_listing_returns_a_preview_not_the_whole_tree(seeded: TestClient) -> None:
    body = seeded.get("/v1/raw/crm/program").json()
    assert body["total"] == 2

    record = body["records"][0]
    assert set(record["preview"]) <= {
        "id",
        "title",
        "status",
        "computed_status",
        "type",
        "target",
        "start_date",
        "capacity",
    }
    assert "sessions" not in record["preview"]
    assert record["size_bytes"] > 0


def test_listing_paginates(seeded: TestClient) -> None:
    first = seeded.get("/v1/raw/crm/program", params={"limit": 1}).json()
    assert len(first["records"]) == 1
    assert first["has_more"] is True

    second = seeded.get("/v1/raw/crm/program", params={"limit": 1, "offset": 1}).json()
    assert second["has_more"] is False
    assert second["records"][0]["source_id"] != first["records"][0]["source_id"]


def test_search_matches_inside_the_payload(seeded: TestClient) -> None:
    """ "Does the CRM know about X?" — the question this exists to answer."""
    hit = seeded.get("/v1/raw/crm/program", params={"q": "Coaching"}).json()
    assert hit["total"] == 1
    assert hit["records"][0]["source_id"] == "91"

    miss = seeded.get("/v1/raw/crm/program", params={"q": "nothing-like-this"}).json()
    assert miss["total"] == 0


def test_an_unknown_entity_is_rejected(seeded: TestClient) -> None:
    assert seeded.get("/v1/raw/crm/not-a-thing").status_code == 422


# ------------------------------------------------------------------ one record
def test_a_single_record_returns_the_whole_payload(seeded: TestClient) -> None:
    body = seeded.get("/v1/raw/crm/program/718").json()
    assert body["payload"]["id"] == 718
    assert len(body["payload"]["sessions"]) == 2
    assert body["payload"]["users"][0]["survey_answers"][0]["answer"] == "Very useful"
    assert body["versions"] == 1


def test_a_missing_record_is_404(seeded: TestClient) -> None:
    assert seeded.get("/v1/raw/crm/program/999999").status_code == 404


# --------------------------------------------------------------------- history
def test_history_shows_every_version(seeded: TestClient, db: Session) -> None:
    land(
        db,
        source=Source.CRM,
        entity=Entity.PROGRAM,
        records=[("718", crm_program.program(capacity=99))],
    )

    body = seeded.get("/v1/raw/crm/program/718/history").json()
    assert len(body["versions"]) == 2
    assert body["versions"][0]["is_current"] is False
    assert body["versions"][-1]["is_current"] is True


def test_history_of_an_unknown_record_is_404(seeded: TestClient) -> None:
    assert seeded.get("/v1/raw/crm/program/999999/history").status_code == 404
