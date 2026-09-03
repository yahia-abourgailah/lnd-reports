"""What ops.sync_run refuses to store, proved against a real PostgreSQL.

The value of this table is not that it records syncs — a log file records
syncs. It is that certain wrong states cannot be written down. A partial unique
index and four CHECKs are the difference between "the runner is careful" and
"carelessness fails loudly", and neither can be demonstrated against SQLite or
against the ORM alone. So these run against Postgres or they skip.

Every test runs inside a transaction the fixture rolls back, so pointing
TEST_DATABASE_URL at the dev database leaves it exactly as it was found.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Connection, select, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from lnd.models import SyncEntity, SyncMode, SyncRun, SyncSource, SyncStatus, SyncTrigger


@pytest.fixture
def session(db_connection: Connection) -> Iterator[Session]:
    with Session(bind=db_connection, join_transaction_mode="create_savepoint") as db_session:
        yield db_session


def _run(**overrides: object) -> SyncRun:
    """A valid in-flight CRM programs run, before any override.

    `started_at` is left to the server default, which is what the runner will
    do — see `_finished_run` for why anything setting `finished_at` must not.
    """
    fields: dict[str, object] = {
        "source": SyncSource.CRM,
        "entity": SyncEntity.PROGRAM,
        "mode": SyncMode.INCREMENTAL,
        "status": SyncStatus.RUNNING,
    }
    fields.update(overrides)
    return SyncRun(**fields)


def _finished_run(status: SyncStatus, *, ran_for: float = 1.0, **overrides: object) -> SyncRun:
    """A run that started `ran_for` seconds ago and has just finished.

    Both timestamps come from one clock, deliberately. Taking `finished_at`
    from Python while leaving `started_at` to the server default mixes two:
    Python computes its value, then the database stamps `now()` a few hundred
    microseconds later, and the row is rejected by
    ck_sync_run_finished_after_started for finishing before it began. Over a
    real thirty-minute sync the gap is invisible; in a test it is most of the
    elapsed time.
    """
    finished = datetime.now(UTC)
    return _run(
        status=status,
        started_at=finished - timedelta(seconds=ran_for),
        finished_at=finished,
        **overrides,
    )


class TestOneRunInFlight:
    def test_a_single_running_row_is_fine(self, session: Session) -> None:
        session.add(_run())
        session.flush()

    def test_a_second_running_row_for_the_same_entity_is_rejected(self, session: Session) -> None:
        """The silent-gap defence.

        Beat fires every 30 minutes and `task_acks_late` redelivers on worker
        loss. Two concurrent runs of the same entity would each advance the
        watermark, and the window between them would be pulled by neither.
        """
        session.add(_run())
        session.flush()

        with pytest.raises(IntegrityError) as err, session.begin_nested():
            session.add(_run())
            session.flush()

        assert "uq_sync_run_one_active" in str(err.value)

    def test_a_different_entity_may_run_concurrently(self, session: Session) -> None:
        """The grain is (source, entity), so CRM entities do not queue behind each other."""
        session.add(_run(entity=SyncEntity.PROGRAM))
        session.add(_run(entity=SyncEntity.SESSION))
        session.add(_run(entity=SyncEntity.ATTENDANCE))
        session.flush()

    def test_the_same_entity_at_a_different_source_may_run_concurrently(
        self, session: Session
    ) -> None:
        session.add(_run(source=SyncSource.CRM, entity=SyncEntity.FEEDBACK))
        session.add(_run(source=SyncSource.FORMS, entity=SyncEntity.FEEDBACK))
        session.flush()

    def test_a_finished_run_frees_the_entity(self, session: Session) -> None:
        """The index constrains running rows only; history accumulates freely.

        This is the shape of a real run: inserted as `running`, updated to a
        terminal state when the pull returns.
        """
        started = datetime.now(UTC)
        first = _run(started_at=started)
        session.add(first)
        session.flush()

        first.status = SyncStatus.SUCCESS
        first.finished_at = started + timedelta(seconds=42)
        session.flush()

        session.add(_run())
        session.flush()

        assert session.scalar(select(SyncRun).where(SyncRun.id == first.id)) is first


class TestStatusAndFinishAgree:
    def test_a_terminal_run_must_have_finished(self, session: Session) -> None:
        with pytest.raises(IntegrityError) as err, session.begin_nested():
            session.add(_run(status=SyncStatus.SUCCESS, finished_at=None))
            session.flush()

        assert "ck_sync_run_terminal_is_finished" in str(err.value)

    def test_a_running_run_must_not_have_finished(self, session: Session) -> None:
        with pytest.raises(IntegrityError) as err, session.begin_nested():
            session.add(_finished_run(SyncStatus.RUNNING))
            session.flush()

        assert "ck_sync_run_terminal_is_finished" in str(err.value)

    @pytest.mark.parametrize("status", [SyncStatus.SUCCESS, SyncStatus.FAILED, SyncStatus.SKIPPED])
    def test_every_terminal_status_is_accepted_with_a_finish_time(
        self, session: Session, status: SyncStatus
    ) -> None:
        session.add(_finished_run(status))
        session.flush()

    def test_a_run_cannot_finish_before_it_started(self, session: Session) -> None:
        started = datetime.now(UTC)
        with pytest.raises(IntegrityError) as err, session.begin_nested():
            session.add(
                _run(
                    status=SyncStatus.SUCCESS,
                    started_at=started,
                    finished_at=started - timedelta(seconds=1),
                )
            )
            session.flush()

        assert "ck_sync_run_finished_after_started" in str(err.value)


class TestDomains:
    """Two layers, and they fail differently on purpose.

    Through the ORM the enum type refuses the value before a statement is ever
    sent — cheap, and the error names the column. The CHECK behind it catches
    everything that does not come through the ORM: a psql session, a data fix,
    a fixture loaded from a file. Both are tested because only the second one
    is a guarantee.
    """

    def test_the_orm_refuses_an_unknown_source_before_the_database_sees_it(
        self, session: Session
    ) -> None:
        with pytest.raises(StatementError) as err, session.begin_nested():
            session.add(_run(source="workday"))
            session.flush()

        assert "workday" in str(err.value)
        assert "sync_source" in str(err.value)

    def test_the_orm_refuses_an_unknown_entity_before_the_database_sees_it(
        self, session: Session
    ) -> None:
        with pytest.raises(StatementError) as err, session.begin_nested():
            session.add(_run(entity="invoices"))
            session.flush()

        assert "invoices" in str(err.value)

    def test_the_check_refuses_an_unknown_source_in_raw_sql(self, session: Session) -> None:
        """The guarantee. Nothing reaches this table without passing the domain."""
        with pytest.raises(IntegrityError) as err, session.begin_nested():
            session.execute(
                text(
                    "INSERT INTO ops.sync_run (source, entity, mode, status) "
                    "VALUES ('workday', 'program', 'incremental', 'running')"
                )
            )

        assert "sync_source" in str(err.value)

    def test_the_check_refuses_an_unknown_status_in_raw_sql(self, session: Session) -> None:
        """Timestamps supplied so that only the domain is in question.

        PostgreSQL reports whichever constraint it evaluates first and does not
        promise an order. An unknown status with a null `finished_at` also
        violates ck_sync_run_terminal_is_finished, which would make this test
        pass or fail for the wrong reason.
        """
        with pytest.raises(IntegrityError) as err, session.begin_nested():
            session.execute(
                text(
                    "INSERT INTO ops.sync_run "
                    "(source, entity, mode, status, started_at, finished_at) "
                    "VALUES ('crm', 'program', 'incremental', 'in_progress', "
                    "now() - interval '1 minute', now())"
                )
            )

        assert "sync_status" in str(err.value)

    def test_counts_cannot_be_negative(self, session: Session) -> None:
        with pytest.raises(IntegrityError) as err, session.begin_nested():
            session.add(_run(records_fetched=-1))
            session.flush()

        assert "ck_sync_run_counts_non_negative" in str(err.value)

    def test_attempts_start_at_one(self, session: Session) -> None:
        """A recorded run was attempted at least once, by definition."""
        with pytest.raises(IntegrityError) as err, session.begin_nested():
            session.add(_run(attempts=0))
            session.flush()

        assert "ck_sync_run_attempts_positive" in str(err.value)


class TestDefaults:
    def test_a_minimal_row_gets_sensible_defaults(self, session: Session) -> None:
        """What the database supplies when the runner supplies nothing."""
        run = _run()
        session.add(run)
        session.flush()
        session.refresh(run)

        assert run.id is not None
        assert run.started_at is not None
        assert run.triggered_by == SyncTrigger.SCHEDULED
        assert run.attempts == 1
        assert run.records_fetched == 0
        assert run.records_written == 0
        assert run.records_deleted == 0
        assert run.finished_at is None
        assert run.details is None

    def test_enum_columns_round_trip_as_their_values(self, session: Session) -> None:
        """Stored as `crm`, not `CRM` — the string the API and the logs use."""
        run = _run(source=SyncSource.HRIS, entity=SyncEntity.EMPLOYEE)
        session.add(run)
        session.flush()

        stored = session.execute(
            select(SyncRun.__table__.c.source, SyncRun.__table__.c.entity).where(
                SyncRun.__table__.c.id == run.id
            )
        ).one()
        assert stored == ("hris", "employee")


class TestWatermarkReadback:
    """Decision one: the watermark is derived from the audit, not stored beside it."""

    def _latest_watermark(
        self, session: Session, source: SyncSource, entity: SyncEntity
    ) -> datetime | None:
        """The query /v1/freshness and the incremental sync both run."""
        return session.scalar(
            select(SyncRun.watermark_to)
            .where(
                SyncRun.source == source,
                SyncRun.entity == entity,
                SyncRun.status == SyncStatus.SUCCESS,
            )
            .order_by(SyncRun.finished_at.desc())
            .limit(1)
        )

    def test_the_latest_success_supplies_the_position(self, session: Session) -> None:
        now = datetime.now(UTC)
        older = now - timedelta(hours=2)
        newer = now - timedelta(minutes=30)

        session.add(
            _run(
                status=SyncStatus.SUCCESS,
                started_at=older,
                finished_at=older,
                watermark_to=older,
            )
        )
        session.add(
            _run(
                status=SyncStatus.SUCCESS,
                started_at=newer,
                finished_at=newer,
                watermark_to=newer,
            )
        )
        session.flush()

        assert self._latest_watermark(session, SyncSource.CRM, SyncEntity.PROGRAM) == newer

    def test_a_later_failure_does_not_move_the_position(self, session: Session) -> None:
        """The last-known-good rule, expressed as a WHERE clause.

        A source that starts erroring must leave the watermark where the last
        good pull left it, or the failed window is skipped on recovery.
        """
        now = datetime.now(UTC)
        good = now - timedelta(hours=1)

        session.add(
            _run(status=SyncStatus.SUCCESS, started_at=good, finished_at=good, watermark_to=good)
        )
        session.add(
            _run(
                status=SyncStatus.FAILED,
                started_at=now,
                finished_at=now,
                error_type="ReadTimeout",
                error_message="CRM did not respond within 30s",
            )
        )
        session.flush()

        assert self._latest_watermark(session, SyncSource.CRM, SyncEntity.PROGRAM) == good

    def test_a_skipped_run_does_not_move_the_position(self, session: Session) -> None:
        """The breaker declining to call must not look like a successful pull."""
        now = datetime.now(UTC)
        good = now - timedelta(hours=1)

        session.add(
            _run(status=SyncStatus.SUCCESS, started_at=good, finished_at=good, watermark_to=good)
        )
        session.add(_run(status=SyncStatus.SKIPPED, started_at=now, finished_at=now))
        session.flush()

        assert self._latest_watermark(session, SyncSource.CRM, SyncEntity.PROGRAM) == good

    def test_an_entity_never_synced_has_no_position(self, session: Session) -> None:
        """Which is how the first incremental knows to ask for everything."""
        position = self._latest_watermark(session, SyncSource.LINKEDIN, SyncEntity.COURSE_ACTIVITY)
        assert position is None
