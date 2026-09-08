"""The PDF pack: the report somebody prints, or forwards without opening.

A PDF is the format this platform loses control of. A spreadsheet is opened by
someone who wanted the numbers; a PDF is attached to a mail, forwarded twice,
and read on a phone by a person who was not in the conversation it came from.
Everything the dashboard says around a figure — its definition, its population,
how fresh it was, what it excludes — has to be *in the file*, and near enough to
the front that it is read before the numbers rather than after an argument.

So the scope page is page one. Not a footer, not an appendix: the first thing.
The definitions follow the figures because they are reference material, but the
sentence saying what this file was computed over comes before anything a reader
could quote.

WHY fpdf2 AND NOT WEASYPRINT

The delivery plan named WeasyPrint, which renders HTML through Pango and Cairo.
Those are system libraries, absent from `python:3.12-slim` and from the machine
this was written on. A PDF writer that runs only inside the container is a PDF
writer the test suite cannot assert, and in this codebase every figure that
leaves the building is pinned by a test. fpdf2 is pure Python: the same bytes in
dev, in CI and in the image. The cost is that layout is written rather than
styled, which for a report of figures and tables is a small one.

The fonts are vendored for the same reason — see `fonts/README.md`. A core PDF
font is Latin-1, and the reconciliation note is made of em dashes.

NOTHING HERE COMPUTES A FIGURE

Every number arrives from `lnd.metrics.registry` or from `lnd.analysis`. This
module decides where things sit on a page and nothing else. An exporter with its
own arithmetic agrees with the dashboard on the day it is written; the first
correction to a population rule leaves the wrong number in the attachment, where
it outlives the screen that could have been checked.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import Align, XPos, YPos
from sqlalchemy.orm import Session

from lnd.analysis import scorecards
from lnd.export import monthly as monthly_report
from lnd.export.provenance import RECONCILIATION_NOTE, Stamp, stamp
from lnd.metrics import registry
from lnd.metrics.base import MetricValue
from lnd.metrics.filters import MetricFilters

FONT_DIR = Path(__file__).parent / "fonts"
FONT = "DejaVu"

#: A4 portrait, in millimetres, with margins wide enough to print.
PAGE_WIDTH = 210.0
MARGIN = 15.0
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN

INK = (17, 26, 27)
MUTED = (102, 120, 122)
RULE = (200, 209, 207)
ACCENT = (20, 103, 92)

#: Figures per row on the summary grid. Three fits a 20pt number and a label
#: without either wrapping, which is the constraint that sets it.
GRID_COLUMNS = 3


class _Document(FPDF):
    """An A4 report with a running head and a numbered foot.

    The head repeats the title and the period on every page. A page that has
    been separated from page one — and printed pages always are — still says
    what it is and what window it covers.
    """

    def __init__(self, title: str, period: str) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.report_title = title
        self.report_period = period
        self.set_margins(MARGIN, MARGIN, MARGIN)
        self.set_auto_page_break(auto=True, margin=18)
        self.add_font(FONT, "", str(FONT_DIR / "DejaVuSans.ttf"))
        self.add_font(FONT, "B", str(FONT_DIR / "DejaVuSans-Bold.ttf"))
        self.set_title(title)
        self.alias_nb_pages()

    def header(self) -> None:
        if self.page_no() == 1:
            return
        self.set_font(FONT, "", 8)
        self.set_text_color(*MUTED)
        self.cell(
            CONTENT_WIDTH * 0.6,
            5,
            self.report_title,
            new_x=XPos.RIGHT,
            new_y=YPos.TOP,
        )
        self.cell(
            CONTENT_WIDTH * 0.4,
            5,
            self.report_period,
            align=Align.R,
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        self.set_draw_color(*RULE)
        self.line(MARGIN, self.get_y(), PAGE_WIDTH - MARGIN, self.get_y())
        self.ln(5)

    def footer(self) -> None:
        self.set_y(-14)
        self.set_font(FONT, "", 7.5)
        self.set_text_color(*MUTED)
        self.cell(
            CONTENT_WIDTH * 0.7,
            4,
            "L&D Analytics Platform — computed from the metric registry",
            new_x=XPos.RIGHT,
            new_y=YPos.TOP,
        )
        self.cell(CONTENT_WIDTH * 0.3, 4, f"{self.page_no()} of {{nb}}", align=Align.R)


# ------------------------------------------------------------------ page parts
def _title(doc: _Document, text: str, subtitle: str = "") -> None:
    doc.set_font(FONT, "B", 20)
    doc.set_text_color(*INK)
    doc.multi_cell(CONTENT_WIDTH, 9, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if subtitle:
        doc.set_font(FONT, "", 10)
        doc.set_text_color(*MUTED)
        doc.multi_cell(CONTENT_WIDTH, 5, subtitle, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    doc.ln(3)


def _section(doc: _Document, text: str) -> None:
    # A heading alone at the foot of a page is a heading for nothing. Break
    # first and let it travel with what it names.
    if doc.get_y() > 240:
        doc.add_page()
    doc.ln(3)
    doc.set_font(FONT, "B", 12)
    doc.set_text_color(*INK)
    doc.multi_cell(CONTENT_WIDTH, 6, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    doc.set_draw_color(*RULE)
    doc.line(MARGIN, doc.get_y() + 1, PAGE_WIDTH - MARGIN, doc.get_y() + 1)
    doc.ln(4)


def _body(doc: _Document, text: str, *, size: float = 9, muted: bool = False) -> None:
    doc.set_font(FONT, "", size)
    doc.set_text_color(*(MUTED if muted else INK))
    doc.multi_cell(CONTENT_WIDTH, size * 0.48, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    doc.ln(1.5)


def _sample_line(value: MetricValue) -> str:
    """What the figure rests on, in words.

    Printed under every number without exception. 98% over 297 responses and
    98% over 4 are different claims, and a PDF is read where the sample cannot
    be hovered for.
    """
    if value.value is None:
        return "not measured"
    if value.numerator is not None and value.denominator is not None:
        return f"{value.numerator:,.0f} of {value.denominator:,.0f}"
    return f"over {value.sample_size:,} rows"


def _figure_grid(doc: _Document, values: Sequence[MetricValue]) -> None:
    """The figures as cards, three to a row.

    A metric the filters put out of reach prints "no value", never a zero. Zero
    is a measurement; an unmeasured metric is not, and a grid of numbers where
    one of them silently means "we could not ask" is the workbook's habit.
    """
    width = CONTENT_WIDTH / GRID_COLUMNS
    for index, value in enumerate(values):
        if index and index % GRID_COLUMNS == 0:
            doc.ln(20)
        if doc.get_y() > 250:
            doc.add_page()
        left = MARGIN + (index % GRID_COLUMNS) * width
        top = doc.get_y()

        doc.set_xy(left, top)
        doc.set_font(FONT, "", 7.5)
        doc.set_text_color(*MUTED)
        doc.cell(width - 3, 4, value.title[:44], new_x=XPos.LEFT, new_y=YPos.NEXT)

        doc.set_x(left)
        doc.set_font(FONT, "B", 17)
        doc.set_text_color(*INK)
        doc.cell(
            width - 3,
            8,
            value.formatted() if value.value is not None else "no value",
            new_x=XPos.LEFT,
            new_y=YPos.NEXT,
        )

        doc.set_x(left)
        doc.set_font(FONT, "", 7)
        doc.set_text_color(*MUTED)
        doc.cell(width - 3, 4, _sample_line(value), new_x=XPos.LEFT, new_y=YPos.NEXT)
        doc.set_xy(left + width, top)
    doc.ln(20)


def _table(
    doc: _Document,
    columns: Sequence[str],
    widths: Sequence[float],
    rows: Sequence[Sequence[str]],
    *,
    aligns: Sequence[str] | None = None,
) -> None:
    """A plain table with a repeating header.

    The header is redrawn after a page break rather than left behind, because a
    column of bare numbers on page four is a column nobody can read.
    """
    align_for = list(aligns or ["L"] * len(columns))

    def head() -> None:
        doc.set_font(FONT, "B", 7.5)
        doc.set_text_color(*MUTED)
        for name, width in zip(columns, widths, strict=True):
            doc.cell(width, 5, name, new_x=XPos.RIGHT, new_y=YPos.TOP)
        doc.ln(5)
        doc.set_draw_color(*RULE)
        doc.line(MARGIN, doc.get_y(), MARGIN + sum(widths), doc.get_y())
        doc.ln(1)

    head()
    doc.set_font(FONT, "", 8)
    doc.set_text_color(*INK)
    for row in rows:
        if doc.get_y() > 262:
            doc.add_page()
            head()
            doc.set_font(FONT, "", 8)
            doc.set_text_color(*INK)
        for cell, width, align in zip(row, widths, align_for, strict=True):
            doc.cell(width, 4.6, cell, new_x=XPos.RIGHT, new_y=YPos.TOP, align=align)
        doc.ln(4.6)
    doc.ln(2)


def _key_values(doc: _Document, pairs: Sequence[tuple[str, str]], label_width: float = 42) -> None:
    """A block of label/value lines.

    Not `_table` with a blank header: an empty header row draws a rule under
    nothing, and the reader spends a moment deciding whether a column name is
    missing.
    """
    for label, value in pairs:
        doc.set_font(FONT, "B", 8.5)
        doc.set_text_color(*INK)
        doc.cell(label_width, 5, label, new_x=XPos.RIGHT, new_y=YPos.TOP)
        doc.set_font(FONT, "", 8.5)
        doc.set_text_color(*MUTED)
        doc.multi_cell(
            CONTENT_WIDTH - label_width,
            5,
            value,
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
            align=Align.L,
        )
        doc.ln(0.5)
    doc.ln(1.5)


def _scope_page(doc: _Document, file_stamp: Stamp, lead: str) -> None:
    """Page one: what this file is, before any number it contains."""
    _title(doc, doc.report_title, doc.report_period)
    _body(doc, lead)

    _section(doc, "What this file was computed over")
    # The definition lines are in the stamp too, but they belong on their own
    # page at the back: seven labels a reader takes in at a glance, not
    # twenty-one paragraphs before the first number.
    _key_values(
        doc,
        [
            (label, value)
            for label, value in file_stamp.as_rows()
            if not label.startswith("Definition")
        ],
    )


def _definitions_page(doc: _Document, values: Sequence[MetricValue]) -> None:
    """Every figure in the file, said in words.

    Carried rather than linked. A link is something a reader has to be able to
    reach, and the reader of a forwarded PDF frequently cannot.
    """
    doc.add_page()
    _section(doc, "What each figure means")
    for value in values:
        doc.set_font(FONT, "B", 9)
        doc.set_text_color(*INK)
        doc.multi_cell(CONTENT_WIDTH, 4.6, value.title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        doc.set_font(FONT, "", 8.5)
        doc.set_text_color(*MUTED)
        text = f"{value.definition} Counted over {value.population.description}."
        spec_note = registry.get(value.key).spec.note
        if spec_note:
            text = f"{text} {spec_note}"
        if value.population.excludes:
            text = f"{text} Excludes: {'; '.join(value.population.excludes)}."
        doc.multi_cell(CONTENT_WIDTH, 4.2, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        doc.ln(2)


def _fmt(value: Decimal | float | int | None, suffix: str = "") -> str:
    if value is None:
        return "—"
    return f"{value:,.1f}{suffix}" if isinstance(value, Decimal | float) else f"{value:,}{suffix}"


# ------------------------------------------------------------------- the packs
def kpi_pack(session: Session, filters: MetricFilters) -> bytes:
    """Every figure the filters allow, as a printable pack."""
    values = list(registry.compute_all(session, filters))
    file_stamp = stamp(session, filters, values)

    doc = _Document("L&D figures", filters.describe())
    doc.add_page()
    _scope_page(
        doc,
        file_stamp,
        "Every figure this platform publishes, computed once from the metric "
        "registry — the same definitions the dashboard uses and the same ones "
        "the CI gate pins. Figures that could not be measured under these "
        "filters say so rather than reading zero.",
    )

    doc.add_page()
    _section(doc, "The figures")
    _figure_grid(doc, values)

    _definitions_page(doc, values)
    return bytes(doc.output())


def program_scorecard_pack(
    session: Session, program_id: int, filters: MetricFilters | None = None
) -> bytes:
    """One programme: its figures, its sessions and every comment on it."""
    card = scorecards.program_scorecard(session, program_id, filters)
    used = filters or MetricFilters()
    file_stamp = stamp(session, used, list(card.figures))

    doc = _Document(f"Programme scorecard — {card.program.title}", used.describe())
    doc.add_page()
    _scope_page(
        doc,
        file_stamp,
        "Every figure below is the platform's own metric narrowed to this one "
        "programme, not a separate calculation. Comments are reproduced in full "
        "and unedited.",
    )

    header = card.program
    _section(doc, "The programme")
    _key_values(
        doc,
        [
            ("Title", header.title),
            ("CRM id", str(header.crm_program_id)),
            ("Type", header.type or "—"),
            ("Target", header.target or "—"),
            ("Department", header.customised_department_name or "—"),
            ("Capacity", _fmt(header.capacity)),
            ("Dates", f"{header.start_date or '—'} to {header.end_date or '—'}"),
            ("Trainers", ", ".join(header.trainer_names) or "not recorded"),
        ],
    )

    _section(doc, "The figures")
    _figure_grid(doc, card.figures)

    _section(doc, f"Sessions ({len(card.sessions)})")
    _table(
        doc,
        ("Date", "Hours", "Trainer", "Location", "Attended"),
        (24, 16, 52, 58, 20),
        [
            (
                str(row.session_date),
                _fmt(row.duration_hours) if row.duration_derivable else "not derivable",
                (row.trainer or "not recorded")[:30],
                (row.location or "—")[:34],
                f"{row.attendees:,}",
            )
            for row in card.sessions
        ],
        aligns=("L", "R", "L", "L", "R"),
    )

    _section(doc, f"What people wrote ({len(card.comments)})")
    if not card.comments:
        _body(doc, "No free-text comments on this programme.", muted=True)
    for comment in card.comments:
        doc.set_font(FONT, "", 7.5)
        doc.set_text_color(*MUTED)
        band = comment.nps_band or "no rating"
        scored = "" if comment.recommend_score is None else f" · scored {comment.recommend_score}"
        doc.multi_cell(
            CONTENT_WIDTH,
            4,
            f"{comment.responded_date or 'no date'} · {band}{scored}",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        doc.set_font(FONT, "", 8.5)
        doc.set_text_color(*INK)
        doc.multi_cell(CONTENT_WIDTH, 4.4, comment.text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        doc.ln(2.5)

    _definitions_page(doc, card.figures)
    return bytes(doc.output())


def trainer_scorecard_pack(
    session: Session, trainer_key: int, filters: MetricFilters | None = None
) -> bytes:
    """One trainer: what they delivered, and how their programmes were rated."""
    card = scorecards.trainer_scorecard(session, trainer_key, filters)
    used = filters or MetricFilters()
    values = list(card.delivery) + list(card.programme_level)
    file_stamp = stamp(session, used, values)

    doc = _Document(f"Trainer scorecard — {card.trainer.canonical_name}", used.describe())
    doc.add_page()

    caveat = (
        "Quality figures on this card are computed over the programmes this "
        "trainer delivered, not attributed to the trainer. A survey response's "
        "grain is one person on one programme and the payload never records "
        "which session the respondent sat in, so on a programme two trainers "
        "shared, both carry the same responses."
    )
    if card.trainer.is_placeholder:
        caveat = (
            "This is not a person. It is what a session names when nobody "
            "recorded who delivered it, and its hours are real and are in every "
            "platform total. "
        ) + caveat
    elif card.trainer.is_external:
        caveat = "This is an outside vendor rather than a colleague. " + caveat

    _scope_page(doc, file_stamp, caveat)

    _section(doc, "Delivered")
    _figure_grid(doc, card.delivery)

    if card.programme_level:
        _section(doc, "How their programmes were rated")
        _figure_grid(doc, card.programme_level)

    if card.nps_by_program:
        _section(doc, "By programme")
        _body(
            doc,
            "The same metric under narrower filters. These sum to the figure "
            "above by construction, not by arithmetic written here.",
            muted=True,
        )
        _table(
            doc,
            ("Programme", "NPS", "Responses"),
            (CONTENT_WIDTH - 50, 25, 25),
            [
                (
                    row.title[:70],
                    row.value.formatted() if row.value.value is not None else "no value",
                    f"{row.value.sample_size:,}",
                )
                for row in card.nps_by_program
            ],
            aligns=("L", "R", "R"),
        )

    _definitions_page(doc, values)
    return bytes(doc.output())


def monthly_pack(session: Session, year: int, month: int, filters: MetricFilters) -> bytes:
    """The monthly report as a PDF: the same nine figures, in the same order.

    The XLSX reproduces the workbook's DASHBOARD grid because recipients open
    it and expect their sheet. A PDF has no such obligation — nobody was
    printing the workbook — so this reads top to bottom instead, with the
    reconciliation note where it cannot be scrolled past.
    """
    window = monthly_report.month_window(year, month)
    period = MetricFilters(
        **{**vars(filters), "date_from": window.date_from, "date_to": window.date_to}
    )

    # The same list the workbook lays out, not a second copy of it. Two
    # spellings of "the figures on the monthly report" is how a metric comes to
    # be on one format and missing from the other.
    figures = list(monthly_report.headline(session, period).values())
    file_stamp = stamp(session, period, figures)
    label = f"{dt.date(year, month, 1):%B %Y}"

    doc = _Document(f"L&D monthly report — {label}", period.describe())
    doc.add_page()
    _scope_page(
        doc,
        file_stamp,
        f"The {label} report. Every figure is computed from the metric registry "
        "over the whole month, and nobody assembled it.",
    )

    doc.add_page()
    _section(doc, label)
    _figure_grid(doc, figures)

    _section(doc, "Reading these against the workbook")
    _body(doc, RECONCILIATION_NOTE)

    _definitions_page(doc, figures)
    return bytes(doc.output())


__all__ = [
    "FONT",
    "FONT_DIR",
    "kpi_pack",
    "monthly_pack",
    "program_scorecard_pack",
    "trainer_scorecard_pack",
]
