"""Opening, stamping and closing a sync run, against a real PostgreSQL.

`record_sync_run` commits on purpose — the audit of a failure has to outlive the
transaction that failed — so unlike the constraint tests these cannot run inside
a transaction that is rolled back. They clean up after themselves by id instead.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from lnd import db
from lnd.config import get_settings
from lnd.models import Entity, Source, SyncMode, SyncRun, SyncStatus, SyncTrigger
from lnd.sync import (
    SyncAlreadyRunning,
    SyncRunRecorder,
    SyncSkipped,
    reap_abandoned_runs,
    record_sync_run,
    watermark_for,
)

CRM = Source.CRM
PROGRAM = Entity.PROGRAM
INCREMENTAL = SyncMode.INCREMENTAL


def _stored(run_id: int) -> SyncRun:
    with db.session_scope() as session:
        run = session.get(SyncRun, run_id)
        assert run is not None
        return run


def _insert_running(started_at: datetime, entity: Entity = PROGRAM) -> int:
    """A `running` row planted directly, standing in for a worker that died."""
    with db.session_scope() as session:
        run = SyncRun(
            source=CRM,
            entity=entity,
            mode=INCREMENTAL,
            status=SyncStatus.RUNNING,
            started_at=started_at,
        )
        session.add(run)
        session.flush()
        return run.id


class TestCounting:
    """Pure bookkeeping — no database needed."""

    def _recorder(self) -> SyncRunRecorder:
        return SyncRunRecorder(
            run_id=1, source=CRM, entity=PROGRAM, mode=INCREMENTAL, watermark_from=None
        )

    def test_counts_accumulate_across_pages(self) -> None:
        recorder = self._recorder()
        recorder.count(fetched=100, written=8)
        recorder.count(fetched=40, written=2)

        assert recorder.records_fetched == 140
        assert recorder.records_written == 10
        assert recorder.records_deleted == 0

    def test_a_negative_count_is_refused_at_the_call_site(self) -> None:
        """Caught here rather than as a CHECK violation when the run closes,
        where the traceback would point at the recorder and not the caller."""
        with pytest.raises(ValueError, match="cannot be negative"):
            self._recorder().count(fetched=-1)

    def test_notes_merge(self) -> None:
        recorder = self._recorder()
        recorder.note(pages=3)
        recorder.note(http_status=200)

        assert recorder.details == {"pages": 3, "http_status": 200}


class TestSuccessfulRun:
    def test_the_row_is_opened_running_and_closed_success(self, live_db: None) -> None:
        with record_sync_run(CRM, PROGRAM, INCREMENTAL) as run:
            assert _stored(run.run_id).status is SyncStatus.RUNNING
            assert _stored(run.run_id).finished_at is None
            run.count(fetched=120, written=8)

        stored = _stored(run.run_id)
        assert stored.status is SyncStatus.SUCCESS
        assert stored.finished_at is not None
        assert stored.records_fetched == 120
        assert stored.records_written == 8
        assert stored.triggered_by is SyncTrigger.SCHEDULED

    def test_both_timestamps_come_from_the_database(self, live_db: None) -> None:
        """So a clock difference between the app and the database cannot make a
        run appear to finish before it started."""
        with record_sync_run(CRM, PROGRAM, INCREMENTAL) as run:
            pass

        stored = _stored(run.run_id)
        assert stored.finished_at is not None
        assert stored.finished_at >= stored.started_at
        assert stored.duration_seconds is not None

    def test_details_are_persisted(self, live_db: None) -> None:
        with record_sync_run(CRM, PROGRAM, INCREMENTAL) as run:
            run.note(pages=4, endpoint="/programs")

        assert _stored(run.run_id).details == {"pages": 4, "endpoint": "/programs"}


class TestWatermark:
    def test_the_first_run_of_an_entity_has_no_position(self, live_db: None) -> None:
        """Which is how it knows to ask the source for everything."""
        with record_sync_run(CRM, Entity.EVALUATION, INCREMENTAL) as run:
            assert run.watermark_from is None

    def test_the_next_run_resumes_from_the_last_position_less_the_overlap(
        self, live_db: None
    ) -> None:
        position = datetime.now(UTC) - timedelta(minutes=10)
        overlap = timedelta(seconds=get_settings().sync_overlap_seconds)

        with record_sync_run(CRM, PROGRAM, INCREMENTAL) as first:
            first.advance_to(position)

        with record_sync_run(CRM, PROGRAM, INCREMENTAL) as second:
            assert second.watermark_from == position - overlap

    def test_a_full_reconcile_advances_the_position_but_reads_no_window(
        self, live_db: None
    ) -> None:
        """It pulls everything by definition, so it has nothing to resume from —
        but having pulled everything, it may say where that got to."""
        position = datetime.now(UTC)

        with record_sync_run(CRM, PROGRAM, SyncMode.FULL_RECONCILE) as run:
            assert run.watermark_from is None
            run.advance_to(position)

        assert _stored(run.run_id).watermark_to == position

    def test_a_backfill_never_writes_a_position(self, live_db: None) -> None:
        """Loading Feb-Aug 2026 must not drag the live position back half a year."""
        with record_sync_run(CRM, PROGRAM, SyncMode.BACKFILL) as run:
            run.advance_to(datetime.now(UTC) - timedelta(days=180))

        assert _stored(run.run_id).watermark_to is None


class TestFailure:
    def test_a_failure_is_recorded_and_re_raised(self, live_db: None) -> None:
        """Re-raised so Celery still sees it and applies the retry policy."""
        with (
            pytest.raises(RuntimeError, match="CRM stopped answering"),
            record_sync_run(CRM, PROGRAM, INCREMENTAL) as run,
        ):
            run.count(fetched=50)
            raise RuntimeError("CRM stopped answering")

        stored = _stored(run.run_id)
        assert stored.status is SyncStatus.FAILED
        assert stored.error_type == "RuntimeError"
        assert stored.error_message == "CRM stopped answering"

    def test_the_counts_reached_before_failing_are_kept(self, live_db: None) -> None:
        """Fifty of a hundred records is the difference between a flaky source
        and one that is refusing outright."""
        with (
            pytest.raises(RuntimeError),
            record_sync_run(CRM, PROGRAM, INCREMENTAL) as run,
        ):
            run.count(fetched=50, written=50)
            raise RuntimeError("half way")

        assert _stored(run.run_id).records_fetched == 50

    def test_a_failure_refuses_to_write_a_position(self, live_db: None) -> None:
        """Even though the body asked it to.

        The read query already filters to successful runs; declining to write
        the column means a failure cannot advance the position even if some
        future query forgets that filter.
        """
        with (
            pytest.raises(RuntimeError),
            record_sync_run(CRM, PROGRAM, INCREMENTAL) as run,
        ):
            run.advance_to(datetime.now(UTC))
            raise RuntimeError("failed after reading a page")

        assert _stored(run.run_id).watermark_to is None

    def test_a_failure_leaves_the_previous_position_intact(self, live_db: None) -> None:
        """The last-known-good rule: a failing source must not cause the window
        it failed on to be skipped when it recovers."""
        good = datetime.now(UTC) - timedelta(hours=1)
        overlap = timedelta(seconds=get_settings().sync_overlap_seconds)

        with record_sync_run(CRM, PROGRAM, INCREMENTAL) as first:
            first.advance_to(good)

        with pytest.raises(RuntimeError), record_sync_run(CRM, PROGRAM, INCREMENTAL):
            raise RuntimeError("down")

        with db.session_scope() as session:
            assert watermark_for(session, CRM, PROGRAM) == good - overlap


class TestSkipped:
    def test_a_skipped_run_is_recorded_and_not_raised(self, live_db: None) -> None:
        """A breaker doing its job is not a task failure, so it must not reach
        Celery and trigger a retry against a source known to be down."""
        with record_sync_run(CRM, PROGRAM, INCREMENTAL) as run:
            raise SyncSkipped("circuit breaker open")

        stored = _stored(run.run_id)
        assert stored.status is SyncStatus.SKIPPED
        assert stored.error_message == "circuit breaker open"

    def test_a_skipped_run_does_not_move_the_position(self, live_db: None) -> None:
        good = datetime.now(UTC) - timedelta(hours=1)
        overlap = timedelta(seconds=get_settings().sync_overlap_seconds)

        with record_sync_run(CRM, PROGRAM, INCREMENTAL) as first:
            first.advance_to(good)

        with record_sync_run(CRM, PROGRAM, INCREMENTAL):
            raise SyncSkipped("still open")

        with db.session_scope() as session:
            assert watermark_for(session, CRM, PROGRAM) == good - overlap


class TestOverlapRefused:
    def test_a_second_run_for_the_same_entity_is_refused(self, live_db: None) -> None:
        """Reported as SyncAlreadyRunning rather than a raw constraint error, so
        a caller can tell "someone else has this" from "the database is broken".
        """
        with (
            record_sync_run(CRM, PROGRAM, INCREMENTAL),
            pytest.raises(SyncAlreadyRunning, match="already in flight"),
            record_sync_run(CRM, PROGRAM, INCREMENTAL),
        ):
            pass  # pragma: no cover - never entered

    def test_an_unrelated_integrity_error_is_not_disguised(self, live_db: None) -> None:
        """Only the overlap constraint becomes SyncAlreadyRunning.

        Anything else surfaces as itself — a caller retrying on "someone else
        has this" must not silently retry on a broken schema instead.
        `attempts=0` violates ck_sync_run_attempts_positive.
        """
        with pytest.raises(IntegrityError), record_sync_run(CRM, PROGRAM, INCREMENTAL, attempts=0):
            pass  # pragma: no cover - never entered

    def test_a_different_entity_is_unaffected(self, live_db: None) -> None:
        with (
            record_sync_run(CRM, PROGRAM, INCREMENTAL),
            record_sync_run(CRM, Entity.SESSION, INCREMENTAL) as other,
        ):
            assert other.run_id is not None


class TestReaper:
    def test_a_recent_running_row_is_left_alone(self, live_db: None) -> None:
        """A slow sync is not a dead one. The threshold sits above Celery's hard
        time limit precisely so this row is protected."""
        _insert_running(datetime.now(UTC) - timedelta(minutes=5))

        with pytest.raises(SyncAlreadyRunning), record_sync_run(CRM, PROGRAM, INCREMENTAL):
            pass  # pragma: no cover - never entered

    def test_an_abandoned_row_is_closed_and_the_entity_freed(self, live_db: None) -> None:
        """The debt owed by uq_sync_run_one_active, paid.

        Without this, one killed worker blocks its entity for good.
        """
        abandoned_id = _insert_running(datetime.now(UTC) - timedelta(hours=3))

        with record_sync_run(CRM, PROGRAM, INCREMENTAL) as run:
            assert run.run_id != abandoned_id

        closed = _stored(abandoned_id)
        assert closed.status is SyncStatus.FAILED
        assert closed.error_type == "AbandonedRun"
        assert closed.finished_at is not None
        assert closed.finished_at >= closed.started_at

    def test_reaping_is_scoped_to_the_entity_asked_for(self, live_db: None) -> None:
        stale = datetime.now(UTC) - timedelta(hours=3)
        mine = _insert_running(stale, entity=PROGRAM)
        theirs = _insert_running(stale, entity=Entity.ATTENDANCE)

        with db.session_scope() as session:
            reaped = reap_abandoned_runs(
                session, older_than=timedelta(hours=1), source=CRM, entity=PROGRAM
            )

        assert reaped == 1
        assert _stored(mine).status is SyncStatus.FAILED
        assert _stored(theirs).status is SyncStatus.RUNNING

    def test_reaping_everything_at_once(self, live_db: None) -> None:
        """The unscoped form, for a periodic sweep."""
        stale = datetime.now(UTC) - timedelta(hours=3)
        _insert_running(stale, entity=PROGRAM)
        _insert_running(stale, entity=Entity.ATTENDANCE)

        with db.session_scope() as session:
            assert reap_abandoned_runs(session, older_than=timedelta(hours=1)) == 2
