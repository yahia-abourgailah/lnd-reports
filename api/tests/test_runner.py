"""One sync, start to finish, against a fake source.

The runner is the order six already-tested things happen in, so what is tested
here is the ordering and the handovers: that the watermark reaches the source as
a window, that what the source returns reaches raw, that the counts recorded
match what actually landed, and that a failure leaves the position alone.

The fake stands in for a real client on purpose. Everything below is true
whatever the CRM does, and none of it needs the CRM to be reachable.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy import select

from lnd import db
from lnd.ingest.models import Entity, RawRecord, Source
from lnd.models import SyncMode, SyncRun, SyncStatus
from lnd.sources.crm.client import CrmClient
from lnd.sync.pullers import CrmProgramPuller, MissingNaturalKey, Record, SourcePuller
from lnd.sync.runner import run_all, run_sync, summarise

CRM = Source.CRM
PROGRAM = Entity.PROGRAM


class Boom(Exception):
    """A transient source failure."""

    retryable = True


class Refused(Exception):
    """A permanent one — a bad credential, say."""

    retryable = False


class FakePuller:
    """A source under the test's control.

    Records what window it was asked for, so the handover from watermark to
    request can be asserted rather than assumed.
    """

    def __init__(
        self,
        records: list[Record] | None = None,
        *,
        entity: Entity = PROGRAM,
        source: Source = CRM,
        raises: Exception | None = None,
        fail_times: int = 0,
    ) -> None:
        self.source = source
        self.entity = entity
        self.transient_errors: tuple[type[Exception], ...] = (Boom, Refused)
        self._records = records if records is not None else []
        self._raises = raises
        self._fail_times = fail_times
        self.calls = 0
        self.windows: list[datetime | None] = []

    def fetch(self, *, changed_since: datetime | None) -> Iterator[Record]:
        self.calls += 1
        self.windows.append(changed_since)

        if self._raises is not None and self.calls <= self._fail_times:
            raise self._raises

        yield from self._records


def _program(identifier: int, **extra: Any) -> Record:
    return (str(identifier), {"id": identifier, "title": f"Program {identifier}", **extra})


def _raw_rows() -> list[RawRecord]:
    with db.session_scope() as session:
        return list(session.scalars(select(RawRecord).order_by(RawRecord.id)).all())


def _stored_run(run_id: int) -> SyncRun:
    with db.session_scope() as session:
        run = session.get(SyncRun, run_id)
        assert run is not None
        return run


class TestContract:
    def test_the_fake_satisfies_the_puller_protocol(self) -> None:
        """If it drifts from the protocol the tests below stop proving anything
        about the real thing."""
        assert isinstance(FakePuller(), SourcePuller)


class TestOnePass:
    def test_records_reach_raw_and_the_counts_match(self, live_db: None) -> None:
        puller = FakePuller([_program(87), _program(88), _program(89)])

        summary = run_sync(puller)

        assert summary.status is SyncStatus.SUCCESS
        assert (summary.fetched, summary.landed, summary.unchanged) == (3, 3, 0)

        rows = _raw_rows()
        assert [row.source_id for row in rows] == ["87", "88", "89"]
        assert rows[0].payload["title"] == "Program 87"

    def test_each_raw_row_points_at_the_run_that_fetched_it(self, live_db: None) -> None:
        """The audit link. Without it, `raw` and `ops` are two piles of rows
        with no way to ask which sync produced which record."""
        summary = run_sync(FakePuller([_program(87)]))

        assert [row.sync_run_id for row in _raw_rows()] == [summary.run_id]

    def test_the_first_pass_asks_for_everything(self, live_db: None) -> None:
        """No watermark yet, so no window — which is how a new entity gets its
        history rather than only what changed since it was switched on."""
        puller = FakePuller([_program(87)])

        run_sync(puller)

        assert puller.windows == [None]

    def test_the_second_pass_asks_from_the_first_pass_position(self, live_db: None) -> None:
        puller = FakePuller([_program(87)])
        first = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

        run_sync(puller, now=first)
        run_sync(puller, now=first + timedelta(minutes=30))

        assert puller.windows[0] is None
        window = puller.windows[1]
        assert window is not None
        # The recorded position, less the configured overlap for clock skew.
        assert window < first

    def test_the_position_is_taken_before_the_fetch(self, live_db: None) -> None:
        """A pull starting at 12:00 and finishing at 12:04 must resume from
        12:00, or a record modified at 12:02 is stepped over and nothing ever
        notices."""
        started = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

        summary = run_sync(FakePuller([_program(87)]), now=started)

        assert _stored_run(summary.run_id).watermark_to == started


class TestIdempotency:
    def test_re_running_the_same_window_lands_nothing_new(self, live_db: None) -> None:
        """Celery redelivers on worker loss, so this happens by design rather
        than by accident. The content hash is what makes it free."""
        puller = FakePuller([_program(87), _program(88)])

        first = run_sync(puller)
        second = run_sync(puller)

        assert (first.landed, second.landed) == (2, 0)
        assert second.fetched == 2
        assert second.unchanged == 2
        assert len(_raw_rows()) == 2

    def test_a_changed_payload_lands_as_a_new_version(self, live_db: None) -> None:
        """Append-only: the old row stays, so what the source said before is
        still answerable."""
        run_sync(FakePuller([_program(87, status="draft")]))
        run_sync(FakePuller([_program(87, status="completed")]))

        rows = _raw_rows()
        assert len(rows) == 2
        assert [row.payload["status"] for row in rows] == ["draft", "completed"]


class TestFailure:
    def test_a_transient_failure_is_retried_and_can_succeed(self, live_db: None) -> None:
        puller = FakePuller([_program(87)], raises=Boom("502"), fail_times=1)

        summary = run_sync(puller)

        assert summary.status is SyncStatus.SUCCESS
        assert puller.calls == 2

    def test_a_permanent_failure_is_not_retried(self, live_db: None) -> None:
        """`Refused.retryable` is False, so the runner takes the source's word
        for it rather than spending the whole backoff budget."""
        puller = FakePuller([_program(87)], raises=Refused("401"), fail_times=99)

        with pytest.raises(Refused):
            run_sync(puller)

        assert puller.calls == 1

    def test_a_failed_pass_is_recorded_and_moves_no_position(self, live_db: None) -> None:
        puller = FakePuller([_program(87)], raises=Refused("401"), fail_times=99)

        with pytest.raises(Refused):
            run_sync(puller)

        with db.session_scope() as session:
            run = session.scalars(select(SyncRun)).one()
        assert run.status is SyncStatus.FAILED
        assert run.error_type == "Refused"
        assert run.watermark_to is None
        assert _raw_rows() == []

    def test_a_failure_does_not_lose_the_previous_position(self, live_db: None) -> None:
        """Last known good: the window that failed must be re-read on recovery,
        not skipped."""
        good = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        run_sync(FakePuller([_program(87)]), now=good)

        with pytest.raises(Refused):
            run_sync(
                FakePuller(raises=Refused("401"), fail_times=99),
                now=good + timedelta(minutes=30),
            )

        recovered = FakePuller([_program(87)])
        run_sync(recovered, now=good + timedelta(hours=1))

        window = recovered.windows[0]
        assert window is not None
        assert window < good


class StubCrmClient:
    """Stands in for `CrmClient`, recording the filters it was called with."""

    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = payloads
        self.filters: dict[str, str] | None = None

    def iter_programs(self, **filters: str) -> Iterator[dict[str, Any]]:
        self.filters = filters
        yield from self.payloads


class TestCrmProgramPuller:
    """The real puller, without the CRM.

    These are the parts the fake cannot prove: how a payload becomes a
    `(source_id, payload)` pair, and what happens to `changed_since` when the
    source cannot narrow by date.
    """

    def test_the_natural_key_is_the_payload_id(self) -> None:
        client = StubCrmClient([{"id": 87, "title": "Hard Talks"}])
        puller = CrmProgramPuller(client=cast(CrmClient, client))

        assert list(puller.fetch(changed_since=None)) == [("87", {"id": 87, "title": "Hard Talks"})]

    def test_a_payload_without_an_identifier_is_refused(self) -> None:
        """Landing it would be worse than failing: a record with no stable key
        cannot be deduplicated, updated or found again."""
        client = StubCrmClient([{"title": "no id"}])
        puller = CrmProgramPuller(client=cast(CrmClient, client))

        with pytest.raises(MissingNaturalKey):
            list(puller.fetch(changed_since=None))

    def test_a_configured_filter_narrows_the_request(self) -> None:
        client = StubCrmClient([{"id": 87}])
        puller = CrmProgramPuller(
            client=cast(CrmClient, client), changed_since_filter="updated_after"
        )
        since = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

        list(puller.fetch(changed_since=since))

        assert client.filters == {"updated_after": since.isoformat()}

    def test_without_a_filter_it_asks_for_everything(self) -> None:
        """The CRM's documented contract is `filter[id]`, `filter[type]` and so
        on; an unsupported filter is a 400, not a silently ignored parameter.
        So with no filter configured the window is dropped rather than guessed
        at, and the content hash stops the extra records costing anything.
        """
        client = StubCrmClient([{"id": 87}])
        puller = CrmProgramPuller(client=cast(CrmClient, client))

        records = list(puller.fetch(changed_since=datetime(2026, 9, 1, tzinfo=UTC)))

        assert client.filters == {}
        assert records == [("87", {"id": 87})]


class TestRunAll:
    def test_every_entity_is_attempted(self, live_db: None) -> None:
        pullers = [
            FakePuller([_program(1)], entity=Entity.PROGRAM),
            FakePuller([_program(2)], entity=Entity.SESSION),
            FakePuller([_program(3)], entity=Entity.ATTENDANCE),
        ]

        summaries = run_all(pullers)

        assert len(summaries) == 3
        assert summarise(summaries) == {
            "entities": 3,
            "fetched": 3,
            # Only a full reconcile can conclude anything is absent, so an
            # incremental always reports zero.
            "deleted": 0,
            "landed": 3,
            "unchanged": 0,
            "skipped": 0,
        }

    def test_one_entity_failing_does_not_stop_the_others(self, live_db: None) -> None:
        """A stuck attendance sync must never mean programs go stale too."""
        pullers = [
            FakePuller([_program(1)], entity=Entity.PROGRAM),
            FakePuller(raises=Refused("401"), fail_times=99, entity=Entity.SESSION),
            FakePuller([_program(3)], entity=Entity.ATTENDANCE),
        ]

        summaries = run_all(pullers)

        assert [summary.entity for summary in summaries] == [
            Entity.PROGRAM,
            Entity.ATTENDANCE,
        ]
        assert len(_raw_rows()) == 2

    def test_the_failure_is_still_recorded(self, live_db: None) -> None:
        """Stepping over it in the loop must not mean losing it from the audit,
        or alerting would never see the source failing."""
        run_all([FakePuller(raises=Refused("401"), fail_times=99, entity=Entity.SESSION)])

        with db.session_scope() as session:
            run = session.scalars(select(SyncRun)).one()
        assert run.status is SyncStatus.FAILED
        assert run.entity is Entity.SESSION


class TestModes:
    def test_a_full_reconcile_asks_for_everything(self, live_db: None) -> None:
        """It has no window by definition — that is what makes it able to see
        what incremental cannot."""
        puller = FakePuller([_program(87)])
        run_sync(puller, now=datetime(2026, 9, 1, 12, 0, tzinfo=UTC))

        run_sync(puller, mode=SyncMode.FULL_RECONCILE)

        assert puller.windows[1] is None

    def test_a_backfill_records_but_moves_no_position(self, live_db: None) -> None:
        summary = run_sync(FakePuller([_program(87)]), mode=SyncMode.BACKFILL)

        stored = _stored_run(summary.run_id)
        assert stored.status is SyncStatus.SUCCESS
        assert stored.watermark_to is None
        assert len(_raw_rows()) == 1


class TestFullReconcileDeletions:
    """FR-A08: a full pass notices what the source has stopped returning.

    `raw` is append-only, so a vanished record keeps every version it ever had
    and simply stops being present — which is what lets week 10 say when a
    program disappeared and which run noticed.
    """

    def test_a_program_that_vanishes_is_marked_absent(self, live_db: None) -> None:
        from lnd.sync.presence import present_ids, vanished

        run_sync(FakePuller([_program(87), _program(88)]), mode=SyncMode.FULL_RECONCILE)
        summary = run_sync(FakePuller([_program(87)]), mode=SyncMode.FULL_RECONCILE)

        assert summary.deleted == 1
        with db.session_scope() as session:
            assert present_ids(session, source=Source.CRM, entity=Entity.PROGRAM) == ["87"]
            gone = vanished(session, source=Source.CRM, entity=Entity.PROGRAM)
            assert [row.source_id for row in gone] == ["88"]
            assert gone[0].vanished_at is not None

    def test_nothing_is_erased_from_raw(self, live_db: None) -> None:
        """The whole point of soft-deleting: the payload stays available."""
        run_sync(FakePuller([_program(87), _program(88)]), mode=SyncMode.FULL_RECONCILE)
        run_sync(FakePuller([_program(87)]), mode=SyncMode.FULL_RECONCILE)

        assert sorted(row.source_id for row in _raw_rows()) == ["87", "88"]

    def test_an_incremental_pass_never_concludes_a_record_is_gone(self, live_db: None) -> None:
        """It sees a subset by construction. Treating that as the whole world
        would soft-delete the catalogue every half hour."""
        from lnd.sync.presence import present_ids

        run_sync(FakePuller([_program(87), _program(88)]), mode=SyncMode.FULL_RECONCILE)
        summary = run_sync(FakePuller([_program(87)]), mode=SyncMode.INCREMENTAL)

        assert summary.deleted == 0
        with db.session_scope() as session:
            assert sorted(present_ids(session, source=Source.CRM, entity=Entity.PROGRAM)) == [
                "87",
                "88",
            ]

    def test_a_returning_program_is_present_again(self, live_db: None) -> None:
        """Programs get unarchived. A resurrection should need no intervention."""
        from lnd.sync.presence import present_ids

        run_sync(FakePuller([_program(87), _program(88)]), mode=SyncMode.FULL_RECONCILE)
        run_sync(FakePuller([_program(87)]), mode=SyncMode.FULL_RECONCILE)
        run_sync(FakePuller([_program(87), _program(88)]), mode=SyncMode.FULL_RECONCILE)

        with db.session_scope() as session:
            assert sorted(present_ids(session, source=Source.CRM, entity=Entity.PROGRAM)) == [
                "87",
                "88",
            ]

    def test_the_count_reaches_the_audit_row(self, live_db: None) -> None:
        """The alert rule watching for an implausible reconcile reads this."""
        run_sync(FakePuller([_program(87), _program(88)]), mode=SyncMode.FULL_RECONCILE)
        summary = run_sync(FakePuller([]), mode=SyncMode.FULL_RECONCILE)

        assert _stored_run(summary.run_id).records_deleted == 2

    def test_one_source_vanishing_does_not_touch_another(self, live_db: None) -> None:
        from lnd.sync.presence import present_ids

        run_sync(FakePuller([_program(87)], entity=Entity.SESSION), mode=SyncMode.FULL_RECONCILE)
        run_sync(FakePuller([_program(88)]), mode=SyncMode.FULL_RECONCILE)
        run_sync(FakePuller([]), mode=SyncMode.FULL_RECONCILE)

        with db.session_scope() as session:
            assert present_ids(session, source=Source.CRM, entity=Entity.PROGRAM) == []
            assert present_ids(session, source=Source.CRM, entity=Entity.SESSION) == ["87"]
