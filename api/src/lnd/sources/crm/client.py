"""Client for the CRM Learning Program Dataset API.

    GET {BASE_URL}/api/learning-integration/programs

One endpoint. Each entry is a whole program with its sessions, attendance,
roster, survey answers and assessment answers nested inside, so a single walk of
the pages is the entire learning history.

Read-only by construction: this class exposes `fetch_programs`, `iter_programs`
and `close`, and a test pins that surface exactly. The endpoint is `GET`-only in
any case — nothing in the integration can change CRM data (§8).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import httpx

from lnd.config import get_settings
from lnd.sources import fixtures
from lnd.sources.crm.models import Program, ProgramsPage

log = logging.getLogger(__name__)

SOURCE = "crm"
PROGRAMS_PATH = "/api/learning-integration/programs"

#: The key goes here and nowhere else. `Authorization` is NOT accepted — it
#: means an OAuth token elsewhere in this API, and a service key sent there
#: reads as missing (§2).
KEY_HEADER = "X-Learning-Key"

#: 120 requests/minute per IP. A 429 backs off and retries.
RATE_LIMIT_PER_MINUTE = 120


class CrmError(RuntimeError):
    """A CRM call failed.

    `retryable` distinguishes a transient fault, where the sync should back off
    and the dashboard keeps serving last-known-good data (FR-A13), from a
    configuration or contract fault, where retrying only wastes the window.
    """

    def __init__(self, message: str, *, retryable: bool = False, code: str | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.code = code


class CrmClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        service_key: str | None = None,
        timeout: float | None = None,
        per_page: int | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = self._normalise_base_url(base_url or settings.crm_base_url)
        self._service_key = service_key or settings.crm_service_key
        self.timeout = timeout or settings.crm_timeout_seconds
        #: 10-25 is a comfortable working size: each entry carries a whole
        #: roster and every answer on it, so a large page is a large response.
        self.per_page = per_page or settings.crm_per_page
        self._client = client
        self._record_fixtures = settings.source_record_fixtures

        if client is None:
            if not self.base_url:
                raise CrmError(
                    "CRM_BASE_URL is not configured.",
                    code="not_configured",
                )
            if not self._service_key:
                raise CrmError(
                    "CRM_SERVICE_KEY is not configured. The CRM team issues it as "
                    "LEARNING_INTEGRATION_SERVICE_KEY on their side.",
                    code="not_configured",
                )

    @staticmethod
    def _normalise_base_url(value: str) -> str:
        """Accept the host with or without a trailing `/api`.

        PROGRAMS_PATH already begins `/api/`, so a base of
        `https://host/api` would produce `/api/api/learning-integration/...`.
        People reasonably copy the address they were given, and that address
        often includes `/api` — so trim it here rather than making a 404 the
        first thing anyone sees.
        """
        trimmed = value.strip().rstrip("/")
        if trimmed.endswith("/api"):
            trimmed = trimmed[: -len("/api")]
        return trimmed

    # ------------------------------------------------------------------ http
    def _headers(self) -> dict[str, str]:
        return {
            KEY_HEADER: self._service_key,
            "Accept": "application/json",
            # The payload is repetitive JSON and compresses well (§8).
            "Accept-Encoding": "gzip",
        }

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout,
                headers=self._headers(),
                follow_redirects=False,
            )
        return self._client

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        """Turn the documented error shapes into a classified CrmError."""
        if response.status_code == httpx.codes.OK:
            return

        code: str | None = None
        message: str | None = None
        try:
            body = response.json()
            if isinstance(body, dict):
                code = body.get("error")
                message = body.get("message")
        except ValueError:
            pass

        detail = code or message or response.reason_phrase

        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            raise CrmError(
                f"CRM rate limit exceeded ({RATE_LIMIT_PER_MINUTE}/min); backing off.",
                retryable=True,
                code="rate_limited",
            )
        if response.status_code == httpx.codes.SERVICE_UNAVAILABLE:
            # integration_not_configured: no key set on the CRM side. Retrying
            # in a tight loop is explicitly called out as the wrong response —
            # somebody has to configure it (§7).
            raise CrmError(
                f"CRM integration is not configured on the server ({detail}). "
                "Contact the CRM team; do not retry in a loop.",
                retryable=False,
                code=code or "integration_not_configured",
            )
        if response.status_code == httpx.codes.UNAUTHORIZED:
            raise CrmError(
                f"CRM rejected the service key ({detail}). Note the key must be sent "
                f"in {KEY_HEADER}; a key in Authorization reads as missing.",
                retryable=False,
                code=code or "unauthorized",
            )
        if response.status_code == httpx.codes.BAD_REQUEST:
            raise CrmError(
                f"CRM rejected the request ({message or detail}). A filter or sort the "
                "repository does not allow.",
                retryable=False,
                code="bad_request",
            )
        if response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
            raise CrmError(
                f"CRM returned {response.status_code} ({detail}).",
                retryable=True,
                code="server_error",
            )
        raise CrmError(
            f"CRM returned {response.status_code} ({detail}).",
            retryable=False,
            code=code or "unexpected_status",
        )

    # ------------------------------------------------------------- fetching
    def _get_page(self, page: int, filters: dict[str, str] | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "per_page": self.per_page}
        for key, value in (filters or {}).items():
            # The repository's own contract: filter[id], filter[type], and so on.
            params[key if key.startswith("filter[") else f"filter[{key}]"] = value

        response = self._http().get(PROGRAMS_PATH, params=params)
        self._raise_for_status(response)

        body = response.json()
        if not isinstance(body, dict) or "programs" not in body or "meta" not in body:
            raise CrmError(
                "CRM response did not carry `programs` and `meta`.",
                retryable=False,
                code="unexpected_shape",
            )
        return body

    def iter_programs(self, **filters: str) -> Iterator[dict[str, Any]]:
        """Yield every program payload, walking the pages.

        Paging follows `meta.has_more_pages` rather than counting rows: the
        endpoint echoes back the page you asked for, so a length-based guess
        would either stop early or loop.

        Order is id descending and stable, so paging is safe (§3).
        """
        page = 1
        seen = 0
        while True:
            body = self._get_page(page, filters)
            programs = body["programs"]
            meta = body["meta"]

            yield from programs
            seen += len(programs)

            log.info(
                "fetched a page of programs",
                extra={
                    "event": "crm.page",
                    "page": meta.get("current_page", page),
                    "returned": len(programs),
                    "total": meta.get("total"),
                },
            )

            if not meta.get("has_more_pages"):
                log.info(
                    "finished walking the dataset",
                    extra={"event": "crm.fetch_complete", "programs": seen},
                )
                return
            page += 1

    def fetch_programs(self, **filters: str) -> list[dict[str, Any]]:
        """Every program payload, as received. Records a fixture if enabled."""
        payloads = list(self.iter_programs(**filters))
        if self._record_fixtures:
            fixtures.record(SOURCE, "program", payloads)
        return payloads

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> CrmClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# parsing — separate from fetching on purpose
# ---------------------------------------------------------------------------
# Raw payloads are landed before anything is parsed. If a model rejects a
# record, the payload is already stored and the failure is diagnosable from the
# database rather than from a log line that has since rotated away.


def parse_programs(
    payloads: list[dict[str, Any]],
) -> tuple[list[Program], list[tuple[dict[str, Any], str]]]:
    """Validate program payloads. Returns (parsed, rejected-with-reason).

    One malformed program must not cost us the other fifty-four.
    """
    parsed: list[Program] = []
    rejected: list[tuple[dict[str, Any], str]] = []
    for payload in payloads:
        try:
            parsed.append(Program.model_validate(payload))
        except ValueError as exc:
            rejected.append((payload, str(exc)))
            log.warning(
                "rejected a program payload",
                extra={"event": "crm.parse_rejected", "program_id": payload.get("id")},
            )
    return parsed, rejected


def parse_page(body: dict[str, Any]) -> ProgramsPage:
    """Validate a whole response, `meta` included."""
    return ProgramsPage.model_validate(body)


def program_id_of(payload: dict[str, Any]) -> str:
    """The natural key for the raw layer, as text."""
    identifier = payload.get("id")
    if identifier is None:
        raise CrmError("A program payload carried no id.", code="missing_id")
    return str(identifier)
