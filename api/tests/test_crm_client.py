"""The CRM client, against a stubbed transport.

The service key has not been issued yet, so the transport is stubbed — but the
header contract, pagination, filters and every documented error code are real
code paths and are exercised here. The day the key arrives, only the base URL
and the key change; these tests keep running unchanged.
"""

from __future__ import annotations

import httpx
import pytest

from lnd.sources.crm import KEY_HEADER, PROGRAMS_PATH, CrmClient, CrmError
from tests.fixtures import crm_program


def client_returning(handler: object, *, per_page: int = 10) -> CrmClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    http = httpx.Client(transport=transport, base_url="https://crm.example.test")
    return CrmClient(
        base_url="https://crm.example.test",
        service_key="test-key",
        client=http,
        per_page=per_page,
    )


# ------------------------------------------------------------------- the shape
def test_a_documented_response_is_read() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == PROGRAMS_PATH
        return httpx.Response(200, json=crm_program.page())

    with client_returning(handler) as crm:
        programs = crm.fetch_programs()

    assert len(programs) == 1
    assert programs[0]["id"] == 718


def test_a_response_missing_programs_or_meta_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    with (
        client_returning(handler) as crm,
        pytest.raises(CrmError, match="programs.*meta") as caught,
    ):
        crm.fetch_programs()
    assert caught.value.code == "unexpected_shape"


def test_an_empty_result_is_not_an_error() -> None:
    """An id matching no program is an empty page, not a 404."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=crm_program.page(programs=[], total=0))

    with client_returning(handler) as crm:
        assert crm.fetch_programs() == []


# --------------------------------------------------------------------- headers
def test_the_key_goes_in_the_learning_key_header() -> None:
    """`Authorization` means an OAuth token elsewhere in this API — a service
    key sent there reads as missing and yields a 401."""
    seen: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        return httpx.Response(200, json=crm_program.page(programs=[]))

    crm = CrmClient(base_url="https://crm.example.test", service_key="s3cret")
    crm._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://crm.example.test",
        headers=crm._headers(),
    )
    with crm:
        crm.fetch_programs()

    assert seen[0][KEY_HEADER] == "s3cret"
    assert "authorization" not in seen[0]
    assert seen[0]["Accept"] == "application/json"


# ------------------------------------------------------------------ pagination
def test_paging_follows_has_more_pages() -> None:
    """Not a row count. The endpoint echoes back the page you asked for, so a
    length-based guess would either stop early or loop forever."""
    pages = {
        1: crm_program.page(
            programs=[crm_program.program(id=1), crm_program.program(id=2)],
            current_page=1,
            total=5,
            last_page=3,
            has_more_pages=True,
        ),
        2: crm_program.page(
            programs=[crm_program.program(id=3), crm_program.program(id=4)],
            current_page=2,
            total=5,
            last_page=3,
            has_more_pages=True,
        ),
        3: crm_program.page(
            programs=[crm_program.program(id=5)],
            current_page=3,
            total=5,
            last_page=3,
            has_more_pages=False,
        ),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[int(request.url.params["page"])])

    with client_returning(handler, per_page=2) as crm:
        assert [p["id"] for p in crm.fetch_programs()] == [1, 2, 3, 4, 5]


def test_a_full_page_that_says_it_is_the_last_stops() -> None:
    """The trap a length-based loop falls into: a final page exactly filled."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(int(request.url.params["page"]))
        return httpx.Response(
            200,
            json=crm_program.page(
                programs=[crm_program.program(id=1), crm_program.program(id=2)],
                total=2,
                has_more_pages=False,
            ),
        )

    with client_returning(handler, per_page=2) as crm:
        crm.fetch_programs()

    assert calls == [1]


def test_per_page_is_sent() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["per_page"])
        return httpx.Response(200, json=crm_program.page(programs=[]))

    with client_returning(handler, per_page=25) as crm:
        crm.fetch_programs()

    assert seen == ["25"]


# --------------------------------------------------------------------- filters
def test_filters_are_sent_in_the_repositorys_contract() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url.query, "utf-8"))
        return httpx.Response(200, json=crm_program.page(programs=[]))

    with client_returning(handler) as crm:
        crm.fetch_programs(current_status="completed")

    assert "filter%5Bcurrent_status%5D=completed" in seen[0]


def test_an_already_bracketed_filter_is_not_double_wrapped() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url.query, "utf-8"))
        return httpx.Response(200, json=crm_program.page(programs=[]))

    with client_returning(handler) as crm:
        crm.fetch_programs(**{"filter[id]": "16"})

    assert "filter%5Bid%5D=16" in seen[0]
    assert "filter%5Bfilter" not in seen[0]


# ---------------------------------------------------------------------- errors
def test_a_missing_key_is_not_retried() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "missing_service_key"})

    with client_returning(handler) as crm, pytest.raises(CrmError) as caught:
        crm.fetch_programs()

    assert caught.value.retryable is False
    assert caught.value.code == "missing_service_key"
    assert KEY_HEADER in str(caught.value)


def test_an_invalid_key_is_not_retried() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_service_key"})

    with client_returning(handler) as crm, pytest.raises(CrmError) as caught:
        crm.fetch_programs()

    assert caught.value.retryable is False


def test_an_unconfigured_integration_is_not_retried() -> None:
    """503 integration_not_configured means nobody set the key on the CRM side.
    The document is explicit: do not retry in a tight loop."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "integration_not_configured"})

    with client_returning(handler) as crm, pytest.raises(CrmError) as caught:
        crm.fetch_programs()

    assert caught.value.retryable is False
    assert caught.value.code == "integration_not_configured"


def test_rate_limiting_is_retryable() -> None:
    """120 requests a minute. Back off and retry rather than fail the sync."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "Too Many Attempts."})

    with client_returning(handler) as crm, pytest.raises(CrmError) as caught:
        crm.fetch_programs()

    assert caught.value.retryable is True
    assert caught.value.code == "rate_limited"


def test_a_rejected_filter_is_not_retryable() -> None:
    """400 names what is allowed. Retrying it just wastes the window."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "Requested filter(s) `sort` are not allowed."})

    with client_returning(handler) as crm, pytest.raises(CrmError) as caught:
        crm.fetch_programs()

    assert caught.value.retryable is False
    assert caught.value.code == "bad_request"


@pytest.mark.parametrize("status", [500, 502, 504])
def test_server_errors_are_retryable(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    with client_returning(handler) as crm, pytest.raises(CrmError) as caught:
        crm.fetch_programs()

    assert caught.value.retryable is True


# -------------------------------------------------------------- configuration
def test_a_missing_base_url_says_so() -> None:
    with pytest.raises(CrmError, match="CRM_BASE_URL"):
        CrmClient(service_key="k")


def test_a_missing_service_key_says_where_it_comes_from() -> None:
    with pytest.raises(CrmError, match="LEARNING_INTEGRATION_SERVICE_KEY"):
        CrmClient(base_url="https://crm.example.test")


# ------------------------------------------------------------------ read-only
def test_the_client_exposes_no_way_to_write() -> None:
    """BRD §5.2 and the API document agree: nothing in this integration can
    change CRM data. Enforced by the absence of a write client, so the absence
    is pinned — adding any method fails this test."""
    surface = {
        name
        for name in dir(CrmClient)
        if not name.startswith("_") and callable(getattr(CrmClient, name))
    }
    forbidden = {"post", "put", "patch", "delete", "create", "update", "write", "save"}
    assert not (surface & forbidden)
    assert surface == {"close", "fetch_programs", "iter_programs"}


# --------------------------------------------------------------- the base URL
@pytest.mark.parametrize(
    "given",
    [
        "https://apicrm.theaddress.app",
        "https://apicrm.theaddress.app/",
        "https://apicrm.theaddress.app/api",
        "https://apicrm.theaddress.app/api/",
        "  https://apicrm.theaddress.app/api  ",
    ],
)
def test_the_base_url_is_accepted_with_or_without_api(given: str) -> None:
    """PROGRAMS_PATH already starts `/api/`. A base that also ends in `/api`
    would produce `/api/api/...`, and the address people are handed usually
    does include it."""
    crm = CrmClient(base_url=given, service_key="k")
    assert crm.base_url == "https://apicrm.theaddress.app"


def test_the_request_path_is_never_doubled() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json=crm_program.page(programs=[]))

    crm = CrmClient(base_url="https://apicrm.theaddress.app/api", service_key="k")
    crm._client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url=crm.base_url, headers=crm._headers()
    )
    with crm:
        crm.fetch_programs()

    assert seen == ["/api/learning-integration/programs"]
