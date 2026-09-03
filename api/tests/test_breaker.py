"""Not calling a source that has stopped answering.

The state is derived from `sync_run` rather than stored, so these tests write
history and read the conclusion back — which is also the point: the breaker's
reasoning is inspectable in the same table that records the syncs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lnd import db
from lnd.models import Entity, Source, SyncMode, SyncRun, SyncStatus
from lnd.sync import SyncSkipped, record_sync_run
from lnd.sync.breaker import BreakerState, BreakerStatus, breaker_status, check_breaker

CRM = Source.CRM
HRIS = Source.HRIS
PROGRAM = Entity.PROGRAM

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
THRESHOLD = 3
COOLDOWN = timedelta(minutes=10)


def _run(
    status: SyncStatus,
    *,
    finished_ago: timedelta,
    source: Source = CRM,
    entity: Entity = PROGRAM,
    error_type: str | None = None,
) -> int:
    with db.session_scope() as session:
        run = SyncRun(
            source=source,
            entity=entity,
            mode=SyncMode.INCREMENTAL,
            status=status,
            started_at=NOW - finished_ago - timedelta(seconds=30),
            finished_at=NOW - finished_ago,
            error_type=error_type,
        )
        session.add(run)
        session.flush()
        return run.id


def _status(source: Source = CRM, *, now: datetime = NOW) -> BreakerStatus:
    with db.session_scope() as session:
        return breaker_status(session, source, now=now, threshold=THRESHOLD, cooldown=COOLDOWN)


def _fail_enough_to_trip(source: Source = CRM, *, newest_ago: timedelta) -> None:
    """Exactly `THRESHOLD` failures in a row, newest at `newest_ago`."""
    for index in range(THRESHOLD):
        _run(
            SyncStatus.FAILED,
            source=source,
            finished_ago=newest_ago + timedelta(minutes=index),
            error_type="ReadTimeout",
        )


class TestClosed:
    def test_no_history_leaves_it_closed(self, live_db: None) -> None:
        """A source that has never run is not a source that is failing."""
        assert _status().state is BreakerState.CLOSED

    def test_failures_below_the_threshold_leave_it_closed(self, live_db: None) -> None:
        _run(SyncStatus.FAILED, finished_ago=timedelta(minutes=1))
        _run(SyncStatus.FAILED, finished_ago=timedelta(minutes=2))

        status = _status()
        assert status.state is BreakerState.CLOSED
        assert status.consecutive_failures == 2

    def test_a_success_resets_the_count(self, live_db: None) -> None:
        """Consecutive means consecutive. Two failures either side of a success
        is not a source that is down."""
        _run(SyncStatus.FAILED, finished_ago=timedelta(minutes=30))
        _run(SyncStatus.FAILED, finished_ago=timedelta(minutes=20))
        _run(SyncStatus.SUCCESS, finished_ago=timedelta(minutes=10))
        _run(SyncStatus.FAILED, finished_ago=timedelta(minutes=1))

        status = _status()
        assert status.state is BreakerState.CLOSED
        assert status.consecutive_failures == 1


class TestOpen:
    def test_enough_failures_open_it(self, live_db: None) -> None:
        _fail_enough_to_trip(newest_ago=timedelta(minutes=1))

        status = _status()
        assert status.state is BreakerState.OPEN
        assert status.consecutive_failures == THRESHOLD
        assert status.last_error_type == "ReadTimeout"

    def test_it_reports_when_the_next_call_is_allowed(self, live_db: None) -> None:
        """Carried so a skipped run can say what tripped it and when it lifts,
        rather than leaving someone to work it out from timestamps."""
        _fail_enough_to_trip(newest_ago=timedelta(minutes=1))

        status = _status()
        assert status.retry_at == NOW - timedelta(minutes=1) + COOLDOWN

    def test_a_failure_of_one_entity_stops_the_others_calling(self, live_db: None) -> None:
        """The reason the scope is the source.

        If the CRM is down then programs, sessions and attendance are all down.
        Five independent breakers would each need their own timeouts to learn
        the same fact.
        """
        for index, entity in enumerate((Entity.PROGRAM, Entity.SESSION, Entity.ATTENDANCE)):
            _run(
                SyncStatus.FAILED,
                entity=entity,
                finished_ago=timedelta(minutes=1 + index),
                error_type="ConnectError",
            )

        assert _status().state is BreakerState.OPEN

    def test_one_source_failing_does_not_open_another(self, live_db: None) -> None:
        _fail_enough_to_trip(source=CRM, newest_ago=timedelta(minutes=1))

        assert _status(CRM).state is BreakerState.OPEN
        assert _status(HRIS).state is BreakerState.CLOSED


class TestHalfOpen:
    def test_the_cooldown_lifts_into_half_open(self, live_db: None) -> None:
        _fail_enough_to_trip(newest_ago=COOLDOWN + timedelta(minutes=1))

        assert _status().state is BreakerState.HALF_OPEN

    def test_it_is_still_open_just_before_the_cooldown_lifts(self, live_db: None) -> None:
        _fail_enough_to_trip(newest_ago=COOLDOWN - timedelta(seconds=1))

        assert _status().state is BreakerState.OPEN

    def test_a_trial_failure_restarts_the_cooldown(self, live_db: None) -> None:
        """So a source that is still down is probed once per cooldown rather
        than on every run."""
        _fail_enough_to_trip(newest_ago=COOLDOWN + timedelta(minutes=5))
        assert _status().state is BreakerState.HALF_OPEN

        _run(SyncStatus.FAILED, finished_ago=timedelta(seconds=30), error_type="ReadTimeout")

        assert _status().state is BreakerState.OPEN

    def test_a_trial_success_closes_it(self, live_db: None) -> None:
        _fail_enough_to_trip(newest_ago=COOLDOWN + timedelta(minutes=5))
        _run(SyncStatus.SUCCESS, finished_ago=timedelta(seconds=30))

        status = _status()
        assert status.state is BreakerState.CLOSED
        assert status.consecutive_failures == 0


class TestSkipsAreNotFailures:
    def test_skipped_runs_do_not_count_towards_the_threshold(self, live_db: None) -> None:
        """Without this the breaker latches open for good: it opens, every run
        is skipped, the skips count as failures, and it never closes again."""
        _run(SyncStatus.SKIPPED, finished_ago=timedelta(minutes=1))
        _run(SyncStatus.SKIPPED, finished_ago=timedelta(minutes=2))
        _run(SyncStatus.SKIPPED, finished_ago=timedelta(minutes=3))
        _run(SyncStatus.SKIPPED, finished_ago=timedelta(minutes=4))

        status = _status()
        assert status.state is BreakerState.CLOSED
        assert status.consecutive_failures == 0

    def test_skips_do_not_hide_the_failures_underneath_them(self, live_db: None) -> None:
        """A skip is neither a failure nor a recovery — it leaves the verdict
        exactly where the real attempts left it."""
        _fail_enough_to_trip(newest_ago=timedelta(minutes=5))
        _run(SyncStatus.SKIPPED, finished_ago=timedelta(minutes=1))

        assert _status().state is BreakerState.OPEN

    def test_a_running_run_does_not_count_either(self, live_db: None) -> None:
        """It has no outcome yet."""
        _run(SyncStatus.FAILED, finished_ago=timedelta(minutes=2))
        _run(SyncStatus.FAILED, finished_ago=timedelta(minutes=3))
        with db.session_scope() as session:
            session.add(
                SyncRun(
                    source=CRM,
                    entity=Entity.SESSION,
                    mode=SyncMode.INCREMENTAL,
                    status=SyncStatus.RUNNING,
                    started_at=NOW,
                )
            )

        assert _status().state is BreakerState.CLOSED


class TestGuard:
    def test_an_open_breaker_raises_and_the_run_records_as_skipped(self, live_db: None) -> None:
        """The whole point, end to end: the source is not called, and the fact
        that it was not called is written down."""
        _fail_enough_to_trip(newest_ago=timedelta(seconds=30))
        called = False

        with record_sync_run(CRM, PROGRAM, SyncMode.INCREMENTAL) as run:
            check_breaker(CRM, now=NOW)
            called = True  # pragma: no cover - unreachable while open

        assert called is False
        with db.session_scope() as session:
            stored = session.get(SyncRun, run.run_id)
            assert stored is not None
            assert stored.status is SyncStatus.SKIPPED
            assert "circuit breaker open" in (stored.error_message or "")

    def test_a_closed_breaker_lets_the_work_happen(self, live_db: None) -> None:
        with record_sync_run(CRM, PROGRAM, SyncMode.INCREMENTAL) as run:
            status = check_breaker(CRM, now=NOW)
            run.note(**status.as_details())
            run.count(fetched=10, written=2)

        with db.session_scope() as session:
            stored = session.get(SyncRun, run.run_id)
            assert stored is not None
            assert stored.status is SyncStatus.SUCCESS
            assert stored.records_fetched == 10
            assert (stored.details or {})["breaker_state"] == "closed"

    def test_half_open_lets_the_trial_call_through(self, live_db: None) -> None:
        """Half open is a permission, not a refusal. If it raised here the
        breaker could never discover that the source had recovered.
        """
        _fail_enough_to_trip(newest_ago=COOLDOWN + timedelta(minutes=5))
        reached = False

        with record_sync_run(CRM, PROGRAM, SyncMode.INCREMENTAL) as run:
            status = check_breaker(CRM, now=NOW)
            reached = True
            run.note(**status.as_details())

        assert reached is True
        assert status.state is BreakerState.HALF_OPEN
        assert status.is_open is False

        with db.session_scope() as session:
            stored = session.get(SyncRun, run.run_id)
            assert stored is not None
            assert stored.status is SyncStatus.SUCCESS
            assert (stored.details or {})["breaker_state"] == "half_open"

    def test_the_skip_reason_names_the_source_and_the_count(self, live_db: None) -> None:
        _fail_enough_to_trip(newest_ago=timedelta(seconds=30))

        with pytest.raises(SyncSkipped, match=f"{THRESHOLD} consecutive failures"):
            check_breaker(CRM, now=NOW)
