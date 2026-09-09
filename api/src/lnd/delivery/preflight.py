"""Check the mail relay now, rather than on the first of the month.

The monthly report sends itself at 07:00 on the 1st. Without this, the first
proof that `SMTP_HOST` and the credentials are right arrives when somebody
notices they did not receive it — and by then the schedule has already run and
recorded a failure against an edition nobody asked for.

So this does everything the real send does except send a report: it resolves the
settings, opens the connection, negotiates TLS, authenticates, and — if asked —
delivers one short message to whoever the report goes to. Ten seconds, and the
answer is a sentence rather than a stack trace.

    python -m lnd.delivery.preflight            check the connection only
    python -m lnd.delivery.preflight --send     and deliver a test message

It is deliberately not an endpoint. Anyone able to trigger mail from a browser
can use this platform to send mail, and there is no reason for that to be
possible.
"""

from __future__ import annotations

import argparse
import logging
import smtplib
import ssl
import sys

from lnd.config import Settings, get_settings
from lnd.delivery import mailer

log = logging.getLogger(__name__)

SUBJECT = "L&D Analytics — delivery test"
BODY = (
    "This is a test from the L&D Analytics Platform.\n\n"
    "If you are reading it, the monthly report will reach you on the first of "
    "the month. Nobody needs to do anything.\n"
)


def describe(settings: Settings) -> list[str]:
    """What the platform believes it has been told, in plain terms.

    Printed before the attempt, because half of the failures here are a setting
    that is simply absent, and reading it back is faster than reading a stack
    trace about it.
    """
    encryption = "STARTTLS" if settings.smtp_starttls else ("SSL" if settings.smtp_ssl else "none")
    return [
        f"  host        {settings.smtp_host or '(not set)'}:{settings.smtp_port}",
        f"  encryption  {encryption}",
        f"  username    {settings.smtp_username or '(none — sending unauthenticated)'}",
        f"  password    {'set' if settings.smtp_password else '(none)'}",
        f"  from        {settings.report_from_address}",
        f"  recipients  {', '.join(settings.recipient_list) or '(not set)'}",
    ]


def check(settings: Settings, *, send: bool = False) -> tuple[bool, str]:
    """Connect, authenticate, optionally send. Never raises; returns the verdict."""
    if not settings.smtp_configured:
        return False, "SMTP_HOST is not set, so nothing is sent and nothing can be."
    if not settings.recipient_list:
        return False, "REPORT_RECIPIENTS is empty, so there is nobody to send to."

    if send:
        delivery = mailer.send(subject=SUBJECT, body=BODY, settings=settings)
        return delivery.sent, delivery.summary

    try:
        with mailer.connect(settings) as connection:
            connection.ehlo()
            if settings.smtp_username:
                connection.login(settings.smtp_username, settings.smtp_password)
    except (OSError, smtplib.SMTPException, ssl.SSLError) as exc:
        # The class name is in the message on purpose: SMTPAuthenticationError
        # and "timed out" send an operator to different places.
        return False, f"{type(exc).__name__}: {exc}"

    return True, (
        "connected"
        + (" and authenticated" if settings.smtp_username else "")
        + " — nothing was sent; add --send to deliver a test message"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that report delivery is configured.")
    parser.add_argument(
        "--send", action="store_true", help="deliver a short test message to the recipients"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    settings = get_settings()
    print("Report delivery is configured as:")
    print("\n".join(describe(settings)))
    print()

    ok, detail = check(settings, send=args.send)
    print(("OK   " if ok else "FAIL ") + detail)
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover - a script
    raise SystemExit(main(sys.argv[1:]))
