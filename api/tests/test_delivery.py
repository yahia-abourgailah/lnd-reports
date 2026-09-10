"""Sending the monthly report, and being able to prove it went.

The only outward-facing action in the platform, on a schedule with nobody
watching. Three properties matter more than the rest:

  * a failed send never costs the report — the edition is stored first;
  * a failed send is *recorded*, with a reason, not swallowed; and
  * re-sending sends the file that was published, not a fresh calculation.

The last is the one that would be got wrong. The instinct after a relay outage
is to re-run the report, and a report re-run in October over August's window is
October's answer for August.

There is a real SMTP conversation in here rather than a mock of `smtplib`. A
mock asserts that we called a function; this asserts that a server received a
message with two attachments and the right recipients, which is the claim.
"""

from __future__ import annotations

import asyncio
import contextlib
import email
import email.policy
import threading
from collections.abc import Iterator
from typing import ClassVar

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from lnd.config import Settings
from lnd.delivery import mailer
from lnd.delivery import monthly as report
from lnd.export import monthly as workbook
from lnd.models.ops import ExportEdition, ExportKind, ExportTrigger

pytestmark = pytest.mark.usefixtures("core_db")

YEAR, MONTH = 2026, 2


# --------------------------------------------------------------- a real server
class _Session(asyncio.Protocol):
    """Enough SMTP to hold one conversation. No auth, no TLS, no queueing."""

    inbox: ClassVar[list[bytes]] = []

    def __init__(self) -> None:
        self.buffer = b""
        self.in_data = False

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        self.transport.write(b"220 test ESMTP\r\n")  # type: ignore[attr-defined]

    def data_received(self, data: bytes) -> None:
        self.buffer += data
        while b"\r\n" in self.buffer and not self.in_data:
            line, self.buffer = self.buffer.split(b"\r\n", 1)
            command = line.upper()
            if command.startswith((b"EHLO", b"HELO")):
                self._write(b"250-test\r\n250 SIZE 100000000\r\n")
            elif command.startswith(b"DATA"):
                self.in_data = True
                self._write(b"354 go ahead\r\n")
            elif command.startswith(b"QUIT"):
                self._write(b"221 bye\r\n")
                self.transport.close()  # type: ignore[attr-defined]
                return
            else:
                self._write(b"250 ok\r\n")
        if self.in_data and b"\r\n.\r\n" in self.buffer:
            body, self.buffer = self.buffer.split(b"\r\n.\r\n", 1)
            _Session.inbox.append(body)
            self.in_data = False
            self._write(b"250 queued\r\n")

    def _write(self, payload: bytes) -> None:
        self.transport.write(payload)  # type: ignore[attr-defined]


@pytest.fixture
def relay() -> Iterator[tuple[int, list[bytes]]]:
    """A live SMTP server on a loopback port, and the messages it received."""
    _Session.inbox = []
    ready = threading.Event()
    port: list[int] = []
    loop = asyncio.new_event_loop()

    async def serve() -> None:
        server = await loop.create_server(_Session, "127.0.0.1", 0)
        port.append(server.sockets[0].getsockname()[1])
        ready.set()
        async with server:
            await server.serve_forever()

    def run() -> None:
        asyncio.set_event_loop(loop)
        # `serve_forever` never returns, so teardown stops the loop out from
        # under it. That is the intended shutdown, and letting the RuntimeError
        # surface would print a traceback per test for a server that did
        # exactly what it was asked to.
        with contextlib.suppress(RuntimeError):
            loop.run_until_complete(serve())

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(5), "the test SMTP server did not start"
    try:
        yield port[0], _Session.inbox
    finally:
        loop.call_soon_threadsafe(loop.stop)


def _settings(port: int | None = None, **overrides: object) -> Settings:
    base: dict[str, object] = {
        "session_secret": "x" * 40,
        "report_from_address": "lnd@example.com",
        "report_recipients": "ld@example.com, director@example.com",
        "smtp_starttls": False,
    }
    if port is not None:
        base |= {"smtp_host": "127.0.0.1", "smtp_port": port}
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


# --------------------------------------------------------------------- mailer
class TestTheMailerRefusesRatherThanPretends:
    def test_no_host_is_not_a_success(self) -> None:
        """A delivery that reports success because sending was switched off is
        the failure discovered in April by somebody asking about March."""
        delivery = mailer.send(subject="x", body="y", settings=_settings())
        assert delivery.sent is False
        assert "no SMTP host" in (delivery.error or "")

    def test_no_recipients_is_not_a_success(self, relay: tuple[int, list[bytes]]) -> None:
        port, _ = relay
        delivery = mailer.send(
            subject="x", body="y", settings=_settings(port, report_recipients="")
        )
        assert delivery.sent is False
        assert "no recipients" in (delivery.error or "")

    def test_an_unreachable_relay_is_reported_not_raised(self) -> None:
        """The scheduled job must not lose a generated report because a relay
        was briefly down — and a task that raises has already thrown away the
        only copy of what it produced."""
        delivery = mailer.send(
            subject="x",
            body="y",
            # Port 1 is reserved and nothing listens on it.
            settings=_settings(1, smtp_host="127.0.0.1"),
        )
        assert delivery.sent is False
        assert delivery.error
        # The class name is in the message: "timed out" and
        # "SMTPAuthenticationError" send an operator to different places.
        assert ":" in delivery.error


class TestARealSend:
    def test_the_message_arrives_with_its_attachments(self, relay: tuple[int, list[bytes]]) -> None:
        port, inbox = relay
        delivery = mailer.send(
            subject="L&D report",
            body="the body",
            attachments=[mailer.Attachment("figures.csv", "text/csv", b"a,b\n1,2\n")],
            settings=_settings(port),
        )
        assert delivery.sent is True
        assert delivery.recipients == ("ld@example.com", "director@example.com")
        assert len(inbox) == 1

        message = email.message_from_bytes(inbox[0], policy=email.policy.default)
        assert message["To"] == "ld@example.com, director@example.com"
        attachments = list(message.iter_attachments())
        assert [part.get_filename() for part in attachments] == ["figures.csv"]
        assert attachments[0].get_payload(decode=True) == b"a,b\n1,2\n"


# ------------------------------------------------------------- the monthly job
class TestTheJob:
    def test_the_edition_is_kept_even_when_the_send_fails(self, loaded: Session) -> None:
        """Generate, keep, then send — in that order.

        Send-first-keep-on-success means a failed send is retried by
        regenerating, and that is a different document.
        """
        result = report.run(loaded, year=YEAR, month=MONTH, settings=_settings())
        assert result.sent is False
        assert result.editions_kept == 1, "the report is kept, as the one PDF it is"

        kept = loaded.scalars(
            select(ExportEdition).where(
                ExportEdition.period_year == YEAR, ExportEdition.period_month == MONTH
            )
        ).all()
        assert len(kept) == 1
        assert all(edition.delivery_error for edition in kept)
        assert all(edition.delivered_at is None for edition in kept)

    def test_a_scheduled_edition_has_no_author(self, loaded: Session) -> None:
        """Null, never a service account. An author column naming the platform
        would make the trail say a person did something no person did."""
        report.run(loaded, year=YEAR, month=MONTH, settings=_settings())
        kept = loaded.scalars(select(ExportEdition)).all()
        assert all(edition.generated_by is None for edition in kept)
        assert all(edition.trigger is ExportTrigger.SCHEDULED for edition in kept)

    def test_a_successful_send_records_who_received_it(
        self, loaded: Session, relay: tuple[int, list[bytes]]
    ) -> None:
        """The addresses as resolved at send time. The distribution list
        changes, and "who got the March report" has no other answer."""
        port, inbox = relay
        result = report.run(loaded, year=YEAR, month=MONTH, settings=_settings(port))
        assert result.sent is True
        assert len(inbox) == 1

        kept = loaded.scalars(select(ExportEdition)).all()
        assert all(edition.delivered_at is not None for edition in kept)
        assert all(
            edition.delivered_to == "ld@example.com, director@example.com" for edition in kept
        )
        assert all(edition.delivery_error is None for edition in kept)

    def test_the_body_quotes_only_figures_the_report_carries(self, loaded: Session) -> None:
        """A body quoting a figure the attachment does not hold is a body
        somebody would query."""
        assert set(report.HEADLINE_IN_BODY) <= set(workbook.REPORT_KEYS)

    def test_a_rerun_with_unchanged_figures_keeps_nothing_new(
        self, loaded: Session, relay: tuple[int, list[bytes]]
    ) -> None:
        port, _ = relay
        report.run(loaded, year=YEAR, month=MONTH, settings=_settings(port))
        again = report.run(loaded, year=YEAR, month=MONTH, settings=_settings(port))
        assert again.editions_kept == 0
        assert again.figures_moved is False

    def test_a_delivered_period_is_not_sent_twice(
        self, loaded: Session, relay: tuple[int, list[bytes]]
    ) -> None:
        """ "The numbers did not move" and "they received it" are different
        facts, and sending again teaches people to ignore the mail."""
        port, inbox = relay
        report.run(loaded, year=YEAR, month=MONTH, settings=_settings(port))
        again = report.run(loaded, year=YEAR, month=MONTH, settings=_settings(port))
        assert again.sent is False
        assert "already delivered" in again.detail
        assert len(inbox) == 1

        forced = report.run(
            loaded, year=YEAR, month=MONTH, settings=_settings(port), force_send=True
        )
        assert forced.sent is True
        assert len(inbox) == 2


class TestResending:
    def test_it_sends_the_stored_bytes(
        self, loaded: Session, relay: tuple[int, list[bytes]]
    ) -> None:
        """The operation retention exists to make possible.

        Regenerating would give the current answer for that month; this sends
        the file that was published.
        """
        port, inbox = relay
        report.run(loaded, year=YEAR, month=MONTH, settings=_settings())
        stored = {
            edition.kind: edition.content for edition in loaded.scalars(select(ExportEdition)).all()
        }

        result = report.resend(loaded, year=YEAR, month=MONTH, settings=_settings(port))
        assert result.sent is True
        assert result.editions_kept == 0, "re-sending generates nothing"

        message = email.message_from_bytes(inbox[0], policy=email.policy.default)
        sent = {
            part.get_filename(): part.get_payload(decode=True)
            for part in message.iter_attachments()
        }
        assert sent[f"lnd-monthly-report-{YEAR}-{MONTH:02d}.pdf"] == stored[ExportKind.MONTHLY_PDF]
        assert list(sent) == [f"lnd-monthly-report-{YEAR}-{MONTH:02d}.pdf"], (
            "one report, one attachment — a second file of the same numbers "
            "leaves the recipient to decide which of the two is the report"
        )

    def test_a_period_with_no_edition_says_so(self, loaded: Session) -> None:
        with pytest.raises(report.NothingToResend):
            report.resend(loaded, year=1999, month=1, settings=_settings())
