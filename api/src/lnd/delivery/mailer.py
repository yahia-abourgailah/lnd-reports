"""Sending a report, and being able to prove afterwards that it went.

The only outward-facing action in the platform. Everything else reads a source
or serves a screen; this puts a file containing employee names into somebody's
mailbox, and it does so on a schedule with no human in the loop. Three things
follow from that, and they are the whole design of this module.

**It refuses rather than pretends.** With no SMTP host configured the send does
not quietly succeed into a log line. It returns a result saying it was not
attempted and why, the caller records that on the edition, and the console shows
"generated, not sent". A delivery that reports success because it was switched
off is the failure that gets discovered when somebody asks why they never got
March.

**It is separate from generating.** The report is generated, stored as an
edition, and only then sent. If the send fails the file still exists and can be
retried or downloaded — a report that has to be regenerated to be re-sent is a
report that comes back with different numbers, because regenerating August in
October gives October's answer for August.

**It says what it sent, to whom, and when.** Recorded against the edition rather
than in a log, because "did the September report go out?" is a question asked
months later by somebody who does not have the logs.

WHY smtplib AND NOT A LIBRARY

One message a month to a handful of addresses on the company relay. The standard
library does SMTP, STARTTLS, implicit TLS and MIME attachments, and the
alternative is a dependency whose value is templating and tracking that nothing
here wants.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage

from lnd.config import Settings, get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Attachment:
    filename: str
    content_type: str
    content: bytes

    @property
    def parts(self) -> tuple[str, str]:
        main, _, sub = self.content_type.partition("/")
        return main or "application", (sub.split(";")[0].strip() or "octet-stream")


@dataclass(frozen=True)
class Delivery:
    """What happened, in terms the edition can store and the console can show."""

    sent: bool
    recipients: tuple[str, ...] = ()
    #: Populated when `sent` is false. Never a bare "failed": the reason is
    #: what tells somebody whether to fix a relay or fill in a setting.
    error: str | None = None
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def summary(self) -> str:
        if self.sent:
            return f"sent to {', '.join(self.recipients)}"
        return self.error or "not sent"


class NotConfigured(RuntimeError):
    """No SMTP host, or nobody to send to."""


def _message(
    *,
    settings: Settings,
    recipients: Sequence[str],
    subject: str,
    body: str,
    attachments: Sequence[Attachment],
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = settings.report_from_address
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(body)
    for attachment in attachments:
        main, sub = attachment.parts
        message.add_attachment(
            attachment.content,
            maintype=main,
            subtype=sub,
            filename=attachment.filename,
        )
    return message


def connect(settings: Settings) -> smtplib.SMTP | smtplib.SMTP_SSL:
    """Open the connection the settings describe.

    `create_default_context` rather than a bare `starttls()`: the default
    verifies the certificate and the hostname, and a relay whose certificate
    does not check out is one this should refuse to hand employee names to.
    """
    if settings.smtp_ssl:
        return smtplib.SMTP_SSL(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
            context=ssl.create_default_context(),
        )
    connection = smtplib.SMTP(
        settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds
    )
    if settings.smtp_starttls:
        connection.starttls(context=ssl.create_default_context())
    return connection


def send(
    *,
    subject: str,
    body: str,
    attachments: Sequence[Attachment] = (),
    recipients: Sequence[str] | None = None,
    settings: Settings | None = None,
) -> Delivery:
    """Send one message. Never raises; returns what happened.

    The scheduled job must not lose a generated report because a relay was
    briefly unreachable, and a task that raises has already thrown away the
    only copy of what it produced. So failures come back as a `Delivery` the
    caller records against the edition, and the file remains downloadable and
    re-sendable.
    """
    settings = settings or get_settings()
    to = list(recipients if recipients is not None else settings.recipient_list)

    if not settings.smtp_configured:
        return Delivery(sent=False, error="no SMTP host configured — the report was not sent")
    if not to:
        return Delivery(sent=False, error="no recipients configured — the report was not sent")

    message = _message(
        settings=settings,
        recipients=to,
        subject=subject,
        body=body,
        attachments=attachments,
    )

    try:
        with connect(settings) as connection:
            if settings.smtp_username:
                connection.login(settings.smtp_username, settings.smtp_password)
            connection.send_message(message)
    except (OSError, smtplib.SMTPException, ssl.SSLError) as exc:
        # The class name is in the message on purpose: "SMTPAuthenticationError"
        # and "timed out" send somebody to different places, and the console
        # shows this string rather than a log nobody will open.
        detail = f"{type(exc).__name__}: {exc}"
        log.warning(
            "report delivery failed",
            extra={"event": "delivery.failed", "recipients": to, "detail": detail},
        )
        return Delivery(sent=False, recipients=tuple(to), error=detail)

    log.info(
        "report delivered",
        extra={
            "event": "delivery.sent",
            "recipients": to,
            "attachments": [a.filename for a in attachments],
        },
    )
    return Delivery(sent=True, recipients=tuple(to))


__all__ = ["Attachment", "Delivery", "NotConfigured", "connect", "send"]
