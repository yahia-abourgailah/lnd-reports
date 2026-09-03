"""Detecting problems, and reporting each one exactly as often as it deserves.

The throttle is the part worth testing hardest: an alerting system that repeats
itself gets muted, and a muted channel is worse than no alerting at all.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from lnd import db
from lnd.alerts import Alert, DispatchResult, dispatch_alerts, evaluate_alerts
from lnd.models import (
    AlertKind,
    AlertNotification,
    AlertSeverity,
    SyncEntity,
    SyncMode,
    SyncRun,
    SyncSource,
    SyncStatus,
)

CRM = SyncSource.CRM
HRIS = SyncSource.HRIS
PROGRAM = SyncEntity.PROGRAM

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
RENOTIFY = timedelta(hours=6)


def _run(
    status: SyncStatus,
    *,
    finished_at: datetime,
    source: SyncSource = CRM,
    entity: SyncEntity = PROGRAM,
    mode: SyncMode = SyncMode.INCREMENTAL,
    deleted: int = 0,
    error_type: str | None = None,
) -> int:
    with db.session_scope() as session:
        run = SyncRun(
            source=source,
            entity=entity,
            mode=mode,
            status=status,
            started_at=finished_at - timedelta(seconds=30),
            finished_at=finished_at,
            watermark_to=finished_at if status is SyncStatus.SUCCESS else None,
            records_deleted=deleted,
            error_type=error_type,
        )
        session.add(run)
        session.flush()
        return run.id


def _ago(**kwargs: float) -> datetime:
    return NOW - timedelta(**kwargs)


def _sync_everything_healthily(
    except_for: tuple[SyncSource, SyncEntity] | None = None,
) -> None:
    """A clean baseline, so a test can isolate the one thing it is about.

    `except_for` leaves one pair untouched, because freshness takes the *newest*
    success — adding an old one on top of a recent one proves nothing.
    """
    for source, entity in (
        (CRM, SyncEntity.PROGRAM),
        (CRM, SyncEntity.SESSION),
        (CRM, SyncEntity.ENROLLMENT),
        (CRM, SyncEntity.ATTENDANCE),
        (HRIS, SyncEntity.EMPLOYEE),
    ):
        if except_for == (source, entity):
            continue
        _run(SyncStatus.SUCCESS, source=source, entity=entity, finished_at=_ago(minutes=5))


def _fail_crm_until_the_breaker_trips() -> None:
    for index in range(3):
        _run(SyncStatus.FAILED, finished_at=_ago(minutes=1 + index), error_type="ReadTimeout")


def _alerts() -> list[Alert]:
    with db.session_scope() as session:
        return evaluate_alerts(session, now=NOW)


def _keys() -> set[str]:
    return {alert.key for alert in _alerts()}


def _stored() -> list[AlertNotification]:
    with db.session_scope() as session:
        return list(session.scalars(select(AlertNotification).order_by(AlertNotification.id)).all())


class Recorder:
    """A sink that remembers what it was asked to deliver."""

    def __init__(self) -> None:
        self.raised: list[str] = []
        self.resolved: list[str] = []

    def __call__(self, alert: Alert, resolved: bool) -> None:
        (self.resolved if resolved else self.raised).append(alert.key)


def _dispatch(
    sink: Recorder, *, now: datetime = NOW, alerts: list[Alert] | None = None
) -> DispatchResult:
    with db.session_scope() as session:
        return dispatch_alerts(session, alerts=alerts, now=now, renotify_after=RENOTIFY, sink=sink)


def _alert(key: str = "data_stale:crm:program", title: str = "something is behind") -> Alert:
    return Alert(
        key=key,
        kind=AlertKind.DATA_STALE,
        severity=AlertSeverity.WARNING,
        title=title,
        detail="detail",
        source=CRM,
        entity=PROGRAM,
    )


class TestRules:
    def test_a_healthy_platform_raises_nothing(self, live_db: None) -> None:
        _sync_everything_healthily()

        assert _alerts() == []

    def test_a_fresh_install_reports_every_expected_entity(self, live_db: None) -> None:
        """Nothing has ever synced, and the catalogue is what makes that
        visible rather than silent."""
        assert _keys() == {
            "never_synced:crm:program",
            "never_synced:crm:session",
            "never_synced:crm:enrollment",
            "never_synced:crm:attendance",
            "never_synced:hris:employee",
        }

    def test_never_synced_is_a_warning_not_a_critical(self, live_db: None) -> None:
        """Before go-live it is the normal state, so it must not read like an
        outage."""
        assert all(alert.severity is AlertSeverity.WARNING for alert in _alerts())

    def test_an_entity_outside_the_catalogue_is_not_reported_missing(self, live_db: None) -> None:
        """Feedback has no declared source while Q-03 is open. Alerting that it
        has never synced would be a permanent false alarm."""
        _sync_everything_healthily()

        assert "never_synced:crm:feedback" not in _keys()
        assert "never_synced:forms:feedback" not in _keys()

    def test_stale_data_is_reported(self, live_db: None) -> None:
        _sync_everything_healthily(except_for=(CRM, SyncEntity.SESSION))
        _run(SyncStatus.SUCCESS, entity=SyncEntity.SESSION, finished_at=_ago(hours=4))

        stale = [alert for alert in _alerts() if alert.kind is AlertKind.DATA_STALE]
        assert [alert.key for alert in stale] == ["data_stale:crm:session"]
        assert "240 minutes behind" in stale[0].title

    def test_a_failing_source_is_critical(self, live_db: None) -> None:
        _sync_everything_healthily()
        _fail_crm_until_the_breaker_trips()

        failing = [alert for alert in _alerts() if alert.kind is AlertKind.SOURCE_FAILING]
        assert [alert.key for alert in failing] == ["source_failing:crm"]
        assert failing[0].severity is AlertSeverity.CRITICAL
        assert "ReadTimeout" in failing[0].detail

    def test_a_failing_source_suppresses_its_own_staleness_alerts(self, live_db: None) -> None:
        """One actionable alert beats a cause buried under five consequences.

        The CRM being down is why its entities are going stale; reporting both
        makes the operator dig for the fact they needed first.
        """
        _sync_everything_healthily(except_for=(CRM, SyncEntity.SESSION))
        _run(SyncStatus.SUCCESS, entity=SyncEntity.SESSION, finished_at=_ago(hours=9))
        _fail_crm_until_the_breaker_trips()

        keys = _keys()
        assert "source_failing:crm" in keys
        assert not any(key.startswith("data_stale:crm") for key in keys)

    def test_another_source_is_unaffected_by_the_suppression(self, live_db: None) -> None:
        _sync_everything_healthily(except_for=(HRIS, SyncEntity.EMPLOYEE))
        _run(
            SyncStatus.SUCCESS,
            source=HRIS,
            entity=SyncEntity.EMPLOYEE,
            finished_at=_ago(hours=4),
        )
        _fail_crm_until_the_breaker_trips()

        assert "data_stale:hris:employee" in _keys()

    def test_a_big_reconcile_delete_is_reported(self, live_db: None) -> None:
        """The 'unexpected difference' the brief asks about."""
        _sync_everything_healthily()
        run_id = _run(
            SyncStatus.SUCCESS,
            mode=SyncMode.FULL_RECONCILE,
            entity=SyncEntity.ATTENDANCE,
            finished_at=_ago(hours=2),
            deleted=140,
        )

        deletes = [alert for alert in _alerts() if alert.kind is AlertKind.RECONCILE_DELETES]
        assert [alert.key for alert in deletes] == [f"reconcile_deletes:{run_id}"]
        assert deletes[0].severity is AlertSeverity.CRITICAL
        assert deletes[0].evidence["records_deleted"] == 140

    def test_a_routine_reconcile_delete_is_not_reported(self, live_db: None) -> None:
        _sync_everything_healthily()
        _run(
            SyncStatus.SUCCESS,
            mode=SyncMode.FULL_RECONCILE,
            finished_at=_ago(hours=2),
            deleted=3,
        )

        assert _alerts() == []

    def test_an_old_reconcile_stops_being_news(self, live_db: None) -> None:
        """Windowed, so it resolves rather than staying live for ever."""
        _sync_everything_healthily()
        _run(
            SyncStatus.SUCCESS,
            mode=SyncMode.FULL_RECONCILE,
            finished_at=_ago(days=3),
            deleted=140,
        )

        assert _alerts() == []

    def test_critical_alerts_sort_first(self, live_db: None) -> None:
        _fail_crm_until_the_breaker_trips()

        severities = [alert.severity for alert in _alerts()]
        assert severities[0] is AlertSeverity.CRITICAL


class TestThrottle:
    def test_a_new_problem_is_sent(self, live_db: None) -> None:
        sink = Recorder()

        result = _dispatch(sink, alerts=[_alert()])

        assert sink.raised == ["data_stale:crm:program"]
        assert result.raised == ["data_stale:crm:program"]

    def test_the_same_problem_is_not_repeated_immediately(self, live_db: None) -> None:
        """The whole point. Evaluated every 15 minutes, a three-day outage would
        otherwise send 288 messages and the channel would be muted."""
        sink = Recorder()
        _dispatch(sink, alerts=[_alert()])

        result = _dispatch(sink, alerts=[_alert()], now=NOW + timedelta(minutes=15))

        assert sink.raised == ["data_stale:crm:program"]
        assert result.suppressed == ["data_stale:crm:program"]

    def test_a_persisting_problem_is_repeated_after_the_interval(self, live_db: None) -> None:
        sink = Recorder()
        _dispatch(sink, alerts=[_alert()])

        result = _dispatch(sink, alerts=[_alert()], now=NOW + RENOTIFY)

        assert sink.raised == ["data_stale:crm:program"] * 2
        assert result.repeated == ["data_stale:crm:program"]

    def test_a_repeat_carries_refreshed_evidence(self, live_db: None) -> None:
        """A lag that has grown from one hour to nine is the useful part of
        saying it again."""
        sink = Recorder()
        _dispatch(sink, alerts=[_alert()])

        _dispatch(
            sink,
            alerts=[_alert(title="now nine hours behind")],
            now=NOW + RENOTIFY,
        )

        assert _stored()[0].title == "now nine hours behind"

    def test_the_send_count_is_kept(self, live_db: None) -> None:
        sink = Recorder()
        _dispatch(sink, alerts=[_alert()])
        _dispatch(sink, alerts=[_alert()], now=NOW + RENOTIFY)

        row = _stored()[0]
        assert row.times_sent == 2
        assert row.first_seen_at == NOW
        assert row.last_sent_at == NOW + RENOTIFY

    def test_two_different_problems_are_throttled_independently(self, live_db: None) -> None:
        sink = Recorder()
        _dispatch(sink, alerts=[_alert("a")])

        result = _dispatch(sink, alerts=[_alert("a"), _alert("b")], now=NOW + timedelta(minutes=5))

        assert result.raised == ["b"]
        assert result.suppressed == ["a"]


class TestResolution:
    def test_a_problem_that_stops_is_reported_resolved(self, live_db: None) -> None:
        sink = Recorder()
        _dispatch(sink, alerts=[_alert()])

        result = _dispatch(sink, alerts=[], now=NOW + timedelta(minutes=20))

        assert sink.resolved == ["data_stale:crm:program"]
        assert result.resolved == ["data_stale:crm:program"]

    def test_resolving_clears_the_throttle(self, live_db: None) -> None:
        """Not a courtesy — a correctness requirement.

        A source that fails, recovers, and fails again twenty minutes later must
        alert again. Without clearing, the leftover throttle from the first
        failure would silence the second.
        """
        sink = Recorder()
        _dispatch(sink, alerts=[_alert()])
        _dispatch(sink, alerts=[], now=NOW + timedelta(minutes=10))

        result = _dispatch(sink, alerts=[_alert()], now=NOW + timedelta(minutes=20))

        assert result.raised == ["data_stale:crm:program"]
        assert sink.raised == ["data_stale:crm:program"] * 2

    def test_a_resolved_row_is_kept_as_history(self, live_db: None) -> None:
        sink = Recorder()
        _dispatch(sink, alerts=[_alert()])
        _dispatch(sink, alerts=[], now=NOW + timedelta(minutes=10))
        _dispatch(sink, alerts=[_alert()], now=NOW + timedelta(minutes=20))

        rows = _stored()
        assert len(rows) == 2
        assert rows[0].resolved_at == NOW + timedelta(minutes=10)
        assert rows[1].resolved_at is None

    def test_only_one_live_row_per_problem_is_possible(self, live_db: None) -> None:
        """Enforced by the database, not by this code being careful."""
        sink = Recorder()
        _dispatch(sink, alerts=[_alert()])

        with pytest.raises(IntegrityError), db.session_scope() as session:
            session.add(
                AlertNotification(
                    alert_key="data_stale:crm:program",
                    kind=AlertKind.DATA_STALE,
                    severity=AlertSeverity.WARNING,
                    title="duplicate",
                    first_seen_at=NOW,
                    last_sent_at=NOW,
                )
            )
            session.flush()


class TestLogSink:
    """The default delivery. Structured lines are what the container runtime
    collects and what monitoring routes on (NFR-08), so the fields matter."""

    def test_a_critical_alert_logs_at_error_with_its_evidence(
        self, live_db: None, caplog: pytest.LogCaptureFixture
    ) -> None:
        _sync_everything_healthily()
        _fail_crm_until_the_breaker_trips()

        with (
            caplog.at_level(logging.INFO, logger="lnd.alerts.notifier"),
            db.session_scope() as session,
        ):
            dispatch_alerts(session, now=NOW)

        raised = [record for record in caplog.records if record.event == "alert.raised"]
        failing = next(record for record in raised if record.alert_key == "source_failing:crm")

        assert failing.levelno == logging.ERROR
        assert failing.severity == "critical"
        assert failing.consecutive_failures == 3

    def test_a_warning_alert_logs_at_warning(
        self, live_db: None, caplog: pytest.LogCaptureFixture
    ) -> None:
        with (
            caplog.at_level(logging.INFO, logger="lnd.alerts.notifier"),
            db.session_scope() as session,
        ):
            dispatch_alerts(session, now=NOW)

        raised = [record for record in caplog.records if record.event == "alert.raised"]
        assert raised
        assert all(record.levelno == logging.WARNING for record in raised)

    def test_a_resolution_logs_its_own_event(
        self, live_db: None, caplog: pytest.LogCaptureFixture
    ) -> None:
        with db.session_scope() as session:
            dispatch_alerts(session, alerts=[_alert()], now=NOW)

        with (
            caplog.at_level(logging.INFO, logger="lnd.alerts.notifier"),
            db.session_scope() as session,
        ):
            dispatch_alerts(session, alerts=[], now=NOW + timedelta(minutes=20))

        resolved = [record for record in caplog.records if record.event == "alert.resolved"]
        assert [record.alert_key for record in resolved] == ["data_stale:crm:program"]

    def test_the_result_counts_everything_delivered(self, live_db: None) -> None:
        sink = Recorder()
        result = _dispatch(sink, alerts=[_alert("a"), _alert("b")])

        assert result.sent == 2


class TestStoredNotification:
    def test_a_live_row_knows_it_is_live(self, live_db: None) -> None:
        sink = Recorder()
        _dispatch(sink, alerts=[_alert()])

        row = _stored()[0]
        assert row.is_live is True
        assert "data_stale:crm:program" in repr(row)

        _dispatch(sink, alerts=[], now=NOW + timedelta(minutes=20))
        assert _stored()[0].is_live is False


class TestEndToEnd:
    def test_an_outage_alerts_once_then_resolves_when_it_recovers(self, live_db: None) -> None:
        """The full life of a problem, driven by the real rules."""
        sink = Recorder()
        _sync_everything_healthily()
        _fail_crm_until_the_breaker_trips()

        first = _dispatch(sink)
        assert "source_failing:crm" in first.raised

        # Fifteen minutes later, still down: nothing new is said.
        second = _dispatch(sink, now=NOW + timedelta(minutes=15))
        assert "source_failing:crm" in second.suppressed

        # It recovers, and the next evaluation says so.
        _run(SyncStatus.SUCCESS, finished_at=NOW + timedelta(minutes=30))
        third = _dispatch(sink, now=NOW + timedelta(minutes=45))

        assert "source_failing:crm" in third.resolved
        assert sink.raised.count("source_failing:crm") == 1
        assert sink.resolved == ["source_failing:crm"]
