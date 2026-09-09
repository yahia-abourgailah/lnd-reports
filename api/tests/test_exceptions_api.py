"""The exception console, and the two alert rules that watch it.

The console's claim is that an operator can act on what they see: every row
carries the rule's meaning, its cost and the form that fixes it, and none of
those sentences are written twice. The tests that matter here are the ones that
would catch a second copy drifting.

Dismissal is the only write. It is the one resolution a pass never undoes, so it
demands a reason and it records who gave it.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from lnd.alerts.rules import evaluate_alerts
from lnd.models.ops import (
    AlertKind,
    DqDisposition,
    DqException,
    DqRule,
    DqStatus,
    ExportEdition,
    ExportKind,
    ExportTrigger,
)
from lnd.quality import catalogue

pytestmark = pytest.mark.usefixtures("live_db")


def _sign_in(client: TestClient) -> None:
    client.get("/v1/auth/login", follow_redirects=True)


class TestTheConsoleServesTheCatalogue:
    def test_every_rule_is_listed_whether_or_not_it_is_firing(
        self, dev_bypass_client: TestClient
    ) -> None:
        """A console listing only what is currently broken cannot answer "what
        does this platform check for?" — the question somebody asks before they
        trust a figure."""
        _sign_in(dev_bypass_client)
        body = dev_bypass_client.get("/v1/exceptions/rules").json()
        assert {row["rule"] for row in body} == {rule.value for rule in DqRule}

    def test_the_served_sentences_are_the_catalogues(self, dev_bypass_client: TestClient) -> None:
        """Not a second copy. The one on screen is the one somebody acts on."""
        _sign_in(dev_bypass_client)
        served = {row["rule"]: row for row in dev_bypass_client.get("/v1/exceptions/rules").json()}
        for rule, spec in catalogue.RULES.items():
            row = served[rule.value]
            assert row["title"] == spec.title
            assert row["means"] == spec.means
            assert row["costs"] == spec.costs
            assert row["resolution"] == spec.resolution
            assert row["costs_numbers"] is spec.costs_numbers
            assert row["fixed_by"] == (spec.fixed_by.value if spec.fixed_by else None)

    def test_signing_in_is_required(self, client: TestClient) -> None:
        assert client.get("/v1/exceptions").status_code == 401
        assert client.get("/v1/exceptions/rules").status_code == 401


class TestTheQueue:
    def test_it_is_grouped_with_losses_first(self, dev_bypass_client: TestClient) -> None:
        _sign_in(dev_bypass_client)
        body = dev_bypass_client.get("/v1/exceptions").json()
        costs = [group["rule"]["costs_numbers"] for group in body["groups"]]
        assert costs == sorted(costs, reverse=True)

    def test_the_split_matches_the_banner(self, dev_bypass_client: TestClient) -> None:
        """The console explains the banner, so disagreeing with it would be the
        one failure a console cannot survive."""
        _sign_in(dev_bypass_client)
        body = dev_bypass_client.get("/v1/exceptions").json()
        assert body["excluded"] == body["excluded_count"]
        assert body["flagged"] == body["flagged_count"]

    def test_the_summary_is_one_line(self, dev_bypass_client: TestClient) -> None:
        _sign_in(dev_bypass_client)
        body = dev_bypass_client.get("/v1/exceptions/summary").json()
        assert body["open"] == body["excluded"] + body["flagged"]


class TestDismissal:
    def _open_one(self, session: Session, key: str = "capacity_exceeded:9001") -> DqException:
        row = DqException(
            exception_key=key,
            rule=DqRule.CAPACITY_EXCEEDED,
            disposition=DqDisposition.COUNTED,
            status=DqStatus.OPEN,
            crm_program_id=9001,
            summary="A test violation.",
        )
        session.add(row)
        session.commit()
        return row

    def test_a_reason_is_required(self, dev_bypass_client: TestClient) -> None:
        """An exception dismissed with no stated reason is indistinguishable
        six months later from one dismissed by mistake."""
        _sign_in(dev_bypass_client)
        response = dev_bypass_client.post(
            "/v1/exceptions/capacity_exceeded%3A9001/dismiss", json={"reason": "  "}
        )
        assert response.status_code == 422

    def test_dismissing_records_who_and_why(self, dev_bypass_client: TestClient) -> None:
        from lnd.db import session_scope

        with session_scope() as session:
            self._open_one(session)

        _sign_in(dev_bypass_client)
        response = dev_bypass_client.post(
            "/v1/exceptions/capacity_exceeded%3A9001/dismiss",
            json={"reason": "The room genuinely took more."},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "dismissed"

        with session_scope() as session:
            row = session.scalar(
                select(DqException).where(DqException.exception_key == "capacity_exceeded:9001")
            )
            assert row is not None
            assert row.closed_by
            assert row.closed_note == "The room genuinely took more."

    def test_an_unknown_key_is_a_404(self, dev_bypass_client: TestClient) -> None:
        _sign_in(dev_bypass_client)
        response = dev_bypass_client.post(
            "/v1/exceptions/nothing%3Ahere/dismiss", json={"reason": "does not exist"}
        )
        assert response.status_code == 404

    def test_a_key_with_colons_survives_the_route(self, dev_bypass_client: TestClient) -> None:
        """`:path` because every exception key contains colons —
        `capacity_exceeded:83` — and a plain segment stops at the first one."""
        from lnd.db import session_scope

        with session_scope() as session:
            self._open_one(session, key="duplicate_attendance:88:4821:2")

        _sign_in(dev_bypass_client)
        response = dev_bypass_client.post(
            "/v1/exceptions/duplicate_attendance%3A88%3A4821%3A2/dismiss",
            json={"reason": "One scan, recorded twice at the door."},
        )
        assert response.status_code == 200


class TestTheAlertsThatWatchTheQueue:
    def test_flagged_records_never_raise_an_exclusion_alert(self) -> None:
        """They are in the figures. Alerting on them would train people to
        ignore the alert that means something."""
        from lnd.db import session_scope

        old = dt.datetime.now(dt.UTC) - dt.timedelta(days=30)
        with session_scope() as session:
            session.add(
                DqException(
                    exception_key="capacity_exceeded:9002",
                    rule=DqRule.CAPACITY_EXCEEDED,
                    disposition=DqDisposition.COUNTED,
                    status=DqStatus.OPEN,
                    summary="Flagged, not excluded.",
                    first_seen_at=old,
                    last_seen_at=old,
                )
            )
            session.commit()

        with session_scope() as session:
            kinds = {alert.kind for alert in evaluate_alerts(session)}
            assert AlertKind.EXCLUSIONS_AGEING not in kinds

    def test_an_ageing_exclusion_does_raise(self) -> None:
        from lnd.db import session_scope

        old = dt.datetime.now(dt.UTC) - dt.timedelta(days=30)
        with session_scope() as session:
            session.add(
                DqException(
                    exception_key="identity_unresolved:crm:9003",
                    rule=DqRule.IDENTITY_UNRESOLVED,
                    disposition=DqDisposition.QUARANTINED,
                    status=DqStatus.OPEN,
                    summary="Nobody could be identified.",
                    first_seen_at=old,
                    last_seen_at=old,
                )
            )
            session.commit()

        with session_scope() as session:
            alerts = [a for a in evaluate_alerts(session) if a.kind is AlertKind.EXCLUSIONS_AGEING]
            assert alerts
            # The message carries the fix, so the alert is actionable without
            # opening the console first.
            assert catalogue.RULES[DqRule.IDENTITY_UNRESOLVED].resolution in alerts[0].detail

    def test_an_undelivered_scheduled_report_raises(self) -> None:
        """The one week-9 failure that is otherwise silent: nothing on a screen
        changes when a mail does not arrive."""
        from lnd.db import session_scope

        with session_scope() as session:
            session.add(
                ExportEdition(
                    kind=ExportKind.MONTHLY_XLSX,
                    trigger=ExportTrigger.SCHEDULED,
                    period_year=2026,
                    period_month=3,
                    filename="lnd-monthly-report-2026-03.xlsx",
                    content_type="application/vnd.ms-excel",
                    filters_applied="2026-03-01 to 2026-03-31",
                    byte_size=10,
                    figures_sha256="d" * 64,
                    content=b"x" * 10,
                    delivery_error="timed out",
                )
            )
            session.commit()

        with session_scope() as session:
            alerts = [a for a in evaluate_alerts(session) if a.kind is AlertKind.REPORT_UNDELIVERED]
            assert alerts
            assert "timed out" in alerts[0].detail

    def test_a_hand_generated_edition_never_raises(self) -> None:
        """It was downloaded, not sent. A null `delivered_at` on a manual
        edition is the truth about it rather than a fault."""
        from lnd.db import session_scope

        with session_scope() as session:
            session.add(
                ExportEdition(
                    kind=ExportKind.MONTHLY_XLSX,
                    trigger=ExportTrigger.MANUAL,
                    period_year=2026,
                    period_month=4,
                    filename="lnd-monthly-report-2026-04.xlsx",
                    content_type="application/vnd.ms-excel",
                    filters_applied="2026-04-01 to 2026-04-30",
                    byte_size=10,
                    figures_sha256="e" * 64,
                    content=b"x" * 10,
                    generated_by="someone@example.com",
                )
            )
            session.commit()

        with session_scope() as session:
            periods = {
                alert.evidence.get("period")
                for alert in evaluate_alerts(session)
                if alert.kind is AlertKind.REPORT_UNDELIVERED
            }
            assert "2026-04" not in periods
