"""How stale is the data — the computation, and the endpoint that serves it.

`now` and `stale_after_seconds` are injected rather than slept through, so a run
can be placed at any age without the suite taking that long to run.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from lnd import db
from lnd.models import SyncEntity, SyncMode, SyncRun, SyncSource, SyncStatus
from lnd.sync.catalogue import EXPECTED_ENTITIES, ordering_key
from lnd.sync.freshness import EntityFreshness, FreshnessResponse, platform_freshness

CRM = SyncSource.CRM
HRIS = SyncSource.HRIS
PROGRAM = SyncEntity.PROGRAM

# A fixed clock, in the past, so nothing here depends on when the suite runs.
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
HOUR = 3600


def _add_run(
    *,
    source: SyncSource = CRM,
    entity: SyncEntity = PROGRAM,
    status: SyncStatus = SyncStatus.SUCCESS,
    finished_ago: timedelta | None = None,
    watermark_to: datetime | None = None,
) -> int:
    """Plant a run at a chosen age relative to NOW."""
    age = finished_ago or timedelta(minutes=1)
    running = status is SyncStatus.RUNNING

    with db.session_scope() as session:
        run = SyncRun(
            source=source,
            entity=entity,
            mode=SyncMode.INCREMENTAL,
            status=status,
            started_at=NOW - age - timedelta(seconds=30),
            finished_at=None if running else NOW - age,
            watermark_to=watermark_to if status is SyncStatus.SUCCESS else None,
        )
        session.add(run)
        session.flush()
        return run.id


def _entity(report: FreshnessResponse, source: SyncSource, entity: SyncEntity) -> EntityFreshness:
    for reported_source in report.sources:
        for reported_entity in reported_source.entities:
            if reported_source.source is source and reported_entity.entity is entity:
                return reported_entity
    raise AssertionError(f"{source}/{entity} missing from the freshness report")


def _report(stale_after_seconds: int | None = None) -> FreshnessResponse:
    with db.session_scope() as session:
        return platform_freshness(session, now=NOW, stale_after_seconds=stale_after_seconds)


def _sign_in(test_client: TestClient) -> None:
    """The dev bypass issues a session straight from /v1/auth/login (Q-14)."""
    test_client.get("/v1/auth/login", follow_redirects=False)


class TestCatalogue:
    def test_every_expected_pair_is_reported_even_with_no_history(self, live_db: None) -> None:
        """The case a history-only report would stay silent about."""
        report = _report()

        for source, entity in EXPECTED_ENTITIES:
            assert _entity(report, source, entity).status == "never_synced"

    def test_the_overall_status_is_never_synced_on_an_empty_platform(self, live_db: None) -> None:
        assert _report().status == "never_synced"

    def test_a_pair_outside_the_catalogue_is_still_reported(self, live_db: None) -> None:
        """Feedback has no declared source while Q-03 is open. If it starts
        syncing anyway it must appear, not be silently dropped."""
        _add_run(entity=SyncEntity.FEEDBACK, finished_ago=timedelta(minutes=5))

        assert _entity(_report(), CRM, SyncEntity.FEEDBACK).status == "ok"

    def test_entities_are_ordered_by_the_pipeline_not_the_alphabet(self) -> None:
        ordered = sorted(EXPECTED_ENTITIES, key=ordering_key)

        assert [entity for source, entity in ordered if source is CRM] == [
            SyncEntity.PROGRAM,
            SyncEntity.SESSION,
            SyncEntity.ENROLLMENT,
            SyncEntity.ATTENDANCE,
        ]


class TestStaleness:
    def test_a_recent_success_is_ok(self, live_db: None) -> None:
        _add_run(finished_ago=timedelta(minutes=10))

        reported = _entity(_report(), CRM, PROGRAM)
        assert reported.status == "ok"
        assert reported.lag_seconds == pytest.approx(600, abs=1)

    def test_an_old_success_is_stale(self, live_db: None) -> None:
        _add_run(finished_ago=timedelta(hours=3))

        assert _entity(_report(), CRM, PROGRAM).status == "stale"

    def test_the_threshold_is_the_boundary(self, live_db: None) -> None:
        """Exactly at the threshold counts as stale — two missed 30-minute runs
        is the condition the alert is defined on."""
        _add_run(finished_ago=timedelta(seconds=HOUR))

        assert _entity(_report(stale_after_seconds=HOUR), CRM, PROGRAM).status == "stale"

    def test_the_threshold_is_reported_so_the_client_need_not_hardcode_it(
        self, live_db: None
    ) -> None:
        assert _report(stale_after_seconds=1800).stale_after_seconds == 1800

    def test_the_newest_success_wins_over_an_older_one(self, live_db: None) -> None:
        _add_run(finished_ago=timedelta(hours=5))
        _add_run(finished_ago=timedelta(minutes=2))

        assert _entity(_report(), CRM, PROGRAM).status == "ok"


class TestLastAttempt:
    def test_a_failure_after_a_good_sync_leaves_the_data_fresh(self, live_db: None) -> None:
        """Status is about staleness only.

        The data really is fresh — it was pulled ten minutes ago. The failure is
        reported alongside rather than folded into the badge, which would make
        one signal mean two things and leave nobody able to act on it.
        """
        _add_run(finished_ago=timedelta(minutes=10))
        _add_run(finished_ago=timedelta(minutes=1), status=SyncStatus.FAILED)

        reported = _entity(_report(), CRM, PROGRAM)
        assert reported.status == "ok"
        assert reported.last_attempt_status is SyncStatus.FAILED

    def test_a_failure_does_not_count_as_a_successful_sync(self, live_db: None) -> None:
        _add_run(finished_ago=timedelta(minutes=1), status=SyncStatus.FAILED)

        reported = _entity(_report(), CRM, PROGRAM)
        assert reported.status == "never_synced"
        assert reported.last_success_at is None
        assert reported.last_attempt_status is SyncStatus.FAILED

    def test_a_run_in_flight_is_flagged(self, live_db: None) -> None:
        _add_run(status=SyncStatus.RUNNING)

        assert _entity(_report(), CRM, PROGRAM).in_flight is True

    def test_the_watermark_is_reported_separately_from_the_sync_time(self, live_db: None) -> None:
        """A sync that ran a minute ago but only pulled up to an hour-old
        position is fresh by one measure and behind by the other."""
        position = NOW - timedelta(hours=1)
        _add_run(finished_ago=timedelta(minutes=1), watermark_to=position)

        reported = _entity(_report(), CRM, PROGRAM)
        assert reported.status == "ok"
        assert reported.data_current_to == position


class TestRollup:
    def test_a_source_takes_the_worst_status_among_its_entities(self, live_db: None) -> None:
        for entity in (SyncEntity.PROGRAM, SyncEntity.SESSION, SyncEntity.ENROLLMENT):
            _add_run(entity=entity, finished_ago=timedelta(minutes=5))
        _add_run(entity=SyncEntity.ATTENDANCE, finished_ago=timedelta(hours=4))
        _add_run(source=HRIS, entity=SyncEntity.EMPLOYEE, finished_ago=timedelta(minutes=5))

        report = _report()
        crm = next(source for source in report.sources if source.source is CRM)
        hris = next(source for source in report.sources if source.source is HRIS)

        assert crm.status == "stale"
        assert hris.status == "ok"
        assert report.status == "stale"

    def test_a_source_reports_the_worst_lag_among_its_entities(self, live_db: None) -> None:
        _add_run(entity=SyncEntity.PROGRAM, finished_ago=timedelta(minutes=5))
        _add_run(entity=SyncEntity.SESSION, finished_ago=timedelta(minutes=45))

        crm = next(source for source in _report().sources if source.source is CRM)
        assert crm.lag_seconds == pytest.approx(45 * 60, abs=1)

    def test_never_synced_outranks_stale(self, live_db: None) -> None:
        """No data at all is a bigger problem than old data, and the one an
        operator has to act on. Only programs synced; the rest never have."""
        _add_run(entity=SyncEntity.PROGRAM, finished_ago=timedelta(hours=4))

        assert _report().status == "never_synced"


class TestEndpoint:
    def test_it_requires_a_signed_in_user(self, client: TestClient) -> None:
        """Unlike /v1/health, which a load balancer must reach before anyone has
        signed in. This describes internal systems."""
        assert client.get("/v1/freshness").status_code == 401

    def test_it_serves_the_report(self, live_db: None, dev_bypass_client: TestClient) -> None:
        _add_run(finished_ago=timedelta(minutes=10))
        _sign_in(dev_bypass_client)

        response = dev_bypass_client.get("/v1/freshness")
        assert response.status_code == 200

        body = response.json()
        assert body["stale_after_seconds"] > 0
        assert {source["source"] for source in body["sources"]} >= {"crm", "hris"}

    def test_a_stale_platform_still_answers_200(
        self, live_db: None, dev_bypass_client: TestClient
    ) -> None:
        """Staleness is the answer, not an error. A 5xx here would make the
        endpoint useless to the badge that has to render it."""
        _add_run(finished_ago=timedelta(days=2))
        _sign_in(dev_bypass_client)

        assert dev_bypass_client.get("/v1/freshness").status_code == 200
