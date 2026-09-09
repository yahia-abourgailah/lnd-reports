"""The monthly report, with no human step (FR-E05).

Eight to twelve hours per cycle, replaced by a beat entry. The job is short
because everything it needs already exists — the registry computes the figures,
`export.monthly` lays out the workbook, `export.pdf` renders the pack,
`export.retention` keeps what was produced — and this only puts them in the
right order.

THE ORDER IS THE DESIGN

    generate  →  keep the edition  →  send  →  record what the send did

Generating and keeping happen before any attempt to send, so a relay that is
down costs a delivery and not a report. The file remains in `/reports`,
downloadable and re-sendable, holding the numbers as they were on the first of
the month. The alternative — send first, keep only on success — means a failed
send is retried by regenerating, and a report regenerated in October over
August's window is October's answer for August. That is the distinction the
whole retention layer exists to preserve, and it would be lost in the one place
it matters most.

WHAT THE JOB DOES WHEN IT IS NOT CONFIGURED

Everything except the last step. With no SMTP host it still generates, still
keeps the edition, and records "not sent" with the reason on it. That is the
honest state and it is visible: the console shows an edition for the period with
nothing in its delivery column. A job that returned success because sending was
switched off would be discovered in April by somebody asking why they never got
March.

RE-RUNNING IS SAFE, AND RE-SENDING IS NOT AUTOMATIC

Retention already refuses to store an edition whose figures match the newest one
for that period, so a re-run over an unchanged month adds no row. But it will
send again, because "the numbers did not move" and "the recipients received it"
are different facts — and the second is the one `delivered_at` records. So the
job skips the send when the period's newest edition already went out, unless it
is asked not to.
"""

from __future__ import annotations

import calendar
import datetime as dt
import logging
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from lnd.config import Settings, get_settings
from lnd.delivery import mailer
from lnd.export import monthly as workbook
from lnd.export import pdf, retention
from lnd.metrics import registry
from lnd.metrics.filters import MetricFilters
from lnd.models.ops import ExportEdition, ExportKind, ExportTrigger
from lnd.quality import completeness as quality

log = logging.getLogger(__name__)

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF_TYPE = "application/pdf"

#: The four figures the mail body quotes, in reading order. Every one is on the
#: report itself — `test_delivery` asserts that, because a body quoting a figure
#: the attachment does not carry is a body somebody would query.
HEADLINE_IN_BODY: tuple[str, ...] = (
    "total_programs",
    "training_days",
    "total_participants",
    "training_hours_delivered",
)


@dataclass
class ReportRun:
    """What one scheduled run produced and what became of it."""

    period: str
    editions_kept: int = 0
    #: False when nothing changed since the last edition of this period, so
    #: nothing new was stored. Not an error — it is the retention rule working.
    figures_moved: bool = True
    sent: bool = False
    recipients: tuple[str, ...] = ()
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["recipients"] = list(self.recipients)
        return data


def _window(year: int, month: int) -> MetricFilters:
    last = calendar.monthrange(year, month)[1]
    return MetricFilters(date_from=dt.date(year, month, 1), date_to=dt.date(year, month, last))


def _body(session: Session, year: int, month: int, period: MetricFilters) -> str:
    """The mail body: what is attached, and what it is not claiming.

    The completeness line is the reason this is not a bare "please find
    attached". A monthly report that arrives with no statement of what was left
    out is the workbook, and the whole platform exists because nobody could see
    what the workbook had dropped.
    """
    label = f"{dt.date(year, month, 1):%B %Y}"
    scoped = quality.completeness(session, period)
    figures = workbook.headline(session, period)

    lines = [
        f"The L&D report for {label}, generated automatically.",
        "",
        "Attached:",
        "  · the workbook, in the layout the dashboard tab has always used",
    ]
    if get_settings().report_attach_pdf:
        lines.append("  · the same figures as a PDF, for forwarding and printing")
    lines += [
        "",
        "Headline figures for the period:",
    ]
    for key in HEADLINE_IN_BODY:
        value = figures.get(key)
        if value is None:
            # The filters put it out of reach, which is a fact worth stating.
            # An absent line would read as "we did not think it worth
            # mentioning" rather than "this could not be measured here".
            lines.append(f"  · {registry.get(key).spec.title}: no value for this period")
        else:
            lines.append(f"  · {value.title}: {value.formatted()}")

    lines += [
        "",
        (
            f"Completeness: {scoped.excluded} record(s) excluded from the figures a rule "
            f"affects, {scoped.flagged} flagged for review and counted in full."
        ),
        (
            "Every figure carries its definition inside the file, and the exception "
            "queue in the platform explains anything excluded."
        ),
        "",
        (
            "Figures restated against the old workbook are corrections, not declines. "
            "The reconciliation note is on the Provenance sheet."
        ),
        "",
        "Generated by the L&D Analytics Platform. Nobody assembled this.",
    ]
    return "\n".join(lines)


def _keep(
    session: Session,
    *,
    kind: ExportKind,
    year: int,
    month: int,
    period: MetricFilters,
    content: bytes,
    media_type: str,
    extension: str,
    digest: str,
) -> tuple[ExportEdition, bool]:
    """Store the edition, and say whether it was new."""
    before = retention.latest_for(session, kind=kind, year=year, month=month)
    edition = retention.keep(
        session,
        kind=kind,
        trigger=ExportTrigger.SCHEDULED,
        year=year,
        month=month,
        filename=f"lnd-monthly-report-{year:04d}-{month:02d}.{extension}",
        content_type=media_type,
        filters_applied=period.describe(),
        content=content,
        figures_sha256=digest,
        # Null, not a service account. Writing "scheduler@platform" into an
        # author column would make the trail say a person did something no
        # person did.
        generated_by=None,
    )
    return edition, before is None or before.id != edition.id


def run(
    session: Session,
    *,
    year: int | None = None,
    month: int | None = None,
    settings: Settings | None = None,
    force_send: bool = False,
) -> ReportRun:
    """Generate, keep and send the monthly report.

    `year`/`month` default to the last complete month — what the first-of-the-
    month beat entry means. `force_send` re-sends a period whose newest edition
    already went out, which is the manual "send it again" and never the
    schedule's behaviour.
    """
    settings = settings or get_settings()
    if year is None or month is None:
        year, month = workbook.previous_month()
    period = _window(year, month)
    label = f"{year:04d}-{month:02d}"

    digest = retention.figures_digest(workbook.headline(session, period).values())
    previous = retention.latest_for(session, kind=ExportKind.MONTHLY_XLSX, year=year, month=month)
    already_delivered = previous is not None and previous.was_delivered

    attachments: list[mailer.Attachment] = []
    kept = 0
    moved = False

    xlsx = workbook.monthly_report(session, period)
    edition, is_new = _keep(
        session,
        kind=ExportKind.MONTHLY_XLSX,
        year=year,
        month=month,
        period=period,
        content=xlsx,
        media_type=XLSX_TYPE,
        extension="xlsx",
        digest=digest,
    )
    kept += int(is_new)
    moved = moved or is_new
    attachments.append(mailer.Attachment(edition.filename, XLSX_TYPE, xlsx))
    editions = [edition]

    if settings.report_attach_pdf:
        printed = pdf.monthly_pack(session, year, month, period)
        pdf_edition, pdf_is_new = _keep(
            session,
            kind=ExportKind.MONTHLY_PDF,
            year=year,
            month=month,
            period=period,
            content=printed,
            media_type=PDF_TYPE,
            extension="pdf",
            digest=digest,
        )
        kept += int(pdf_is_new)
        moved = moved or pdf_is_new
        attachments.append(mailer.Attachment(pdf_edition.filename, PDF_TYPE, printed))
        editions.append(pdf_edition)

    run_result = ReportRun(period=label, editions_kept=kept, figures_moved=moved)

    if already_delivered and not force_send:
        # The numbers have not moved and the recipients already have this one.
        # Sending it again teaches people to ignore the mail, which costs more
        # than a missed duplicate.
        run_result.detail = "already delivered; nothing changed since"
        log.info(
            "monthly report already delivered",
            extra={"event": "reports.monthly.skipped", "period": label},
        )
        return run_result

    delivery = mailer.send(
        subject=f"L&D report — {dt.date(year, month, 1):%B %Y}",
        body=_body(session, year, month, period),
        attachments=attachments,
        settings=settings,
    )

    for stored in editions:
        stored.delivered_at = delivery.at if delivery.sent else None
        stored.delivered_to = ", ".join(delivery.recipients) or None
        stored.delivery_error = None if delivery.sent else delivery.error

    run_result.sent = delivery.sent
    run_result.recipients = delivery.recipients
    run_result.detail = delivery.summary

    log.info(
        "monthly report run",
        extra={"event": "reports.monthly.complete", **run_result.as_dict()},
    )
    return run_result


class NothingToResend(LookupError):
    """No edition was ever kept for that period."""


def resend(
    session: Session,
    *,
    year: int,
    month: int,
    settings: Settings | None = None,
) -> ReportRun:
    """Send the stored editions for a period again, without regenerating them.

    The operation the whole retention layer exists to make possible. After a
    relay outage the obvious instinct is to re-run the report — and that would
    produce October's answer for August, so the recipients would receive a
    document that differs from the one the platform says it published.

    This reads the bytes that were kept and sends those. The figures are as they
    were on the first of the month, whatever has been corrected since.
    """
    settings = settings or get_settings()
    editions = list(
        session.scalars(
            select(ExportEdition)
            .where(
                ExportEdition.period_year == year,
                ExportEdition.period_month == month,
            )
            .order_by(ExportEdition.kind, ExportEdition.generated_at.desc())
        ).all()
    )
    # Newest per format: a period may hold up to three editions of each, and
    # re-sending all of them would deliver the same report several times.
    newest: dict[ExportKind, ExportEdition] = {}
    for edition in editions:
        newest.setdefault(edition.kind, edition)

    if not newest:
        raise NothingToResend(
            f"no edition was kept for {year:04d}-{month:02d}; there is nothing to re-send"
        )

    label = f"{year:04d}-{month:02d}"
    delivery = mailer.send(
        subject=f"L&D report — {dt.date(year, month, 1):%B %Y}",
        body=(
            f"The L&D report for {dt.date(year, month, 1):%B %Y}, re-sent.\n\n"
            "These are the files as they were first published, not a fresh "
            "calculation — the figures are the ones the platform recorded for "
            "that month, whatever has been corrected since."
        ),
        attachments=[
            mailer.Attachment(edition.filename, edition.content_type, edition.content)
            for edition in newest.values()
        ],
        settings=settings,
    )

    for edition in newest.values():
        edition.delivered_at = delivery.at if delivery.sent else None
        edition.delivered_to = ", ".join(delivery.recipients) or None
        edition.delivery_error = None if delivery.sent else delivery.error

    log.info(
        "monthly report re-sent",
        extra={
            "event": "reports.monthly.resent",
            "period": label,
            "sent": delivery.sent,
            "detail": delivery.summary,
        },
    )
    return ReportRun(
        period=label,
        editions_kept=0,
        figures_moved=False,
        sent=delivery.sent,
        recipients=delivery.recipients,
        detail=delivery.summary,
    )


__all__ = ["NothingToResend", "ReportRun", "resend", "run"]
