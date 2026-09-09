"""Generate the reconciliation statement from the registry.

    python -m lnd.reference.reconcile        # writes docs/reconciliation.md

Task 4 of week 4, and the document the stakeholder gate turns on. Generated
rather than written because every part of it already exists in code: each metric
carries its definition, its population, how it relates to the workbook's figure
(`Provenance`) and a note saying what changed and why (`MetricSpec.note`). A
hand-written statement would be a second copy of all that, free to drift from
the first, and the drift would be discovered in the meeting.

WHAT IS NOT GENERATED

The workbook's own published figures. Those are constants below, transcribed
from the file and the BRD, each carrying where it came from — there is no
machine-readable source for them and pretending otherwise would be worse than
typing them once with provenance attached.

TWO COLUMNS, AND THE ORDER MATTERS

The workbook covers February to August 2026. The platform covers July 2025 to
September 2026. Comparing the workbook's column against the full dataset makes
every figure look enormously larger and attributes to *correction* what is
mostly *coverage* — so the like-for-like window is presented first and the full
dataset second, labelled as not comparable.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from lnd.metrics import registry
from lnd.metrics.base import MetricValue, Provenance
from lnd.reference.windows import WORKBOOK

log = logging.getLogger(__name__)

STATEMENT = Path(__file__).resolve().parents[4] / "docs" / "reconciliation.md"


@dataclass(frozen=True)
class Published:
    """What the workbook actually printed, and where that came from.

    `source` is carried per figure rather than stated once, because the two
    sources disagree: the BRD's reconciliation table records Total Programs as
    24 and the week-4 handover as 25. A single number with no provenance would
    hide that; this way the disagreement is visible in the statement, which is
    where somebody can settle it.
    """

    value: str
    source: str
    note: str = ""


#: Transcribed by hand. Everything else in this document is computed.
WORKBOOK_FIGURES: dict[str, Published] = {
    "total_programs": Published(
        "24", "BRD v1.5", "the week-4 handover records 25 — worth settling with L&D"
    ),
    "training_days": Published("40", "BRD v1.5"),
    "training_hours_delivered": Published("130.5 hours", "BRD v1.5"),
    "learner_hours": Published("1,386.0 hours", "BRD v1.5"),
    "total_participants": Published("128", "BRD v1.5"),
    "nps": Published("92.7%", "BRD v1.5", "published as a percentage; NPS is an index"),
    "knowledge_relevance": Published("100.0%", "BRD v1.5"),
    "activity_effectiveness": Published("98.2%", "BRD v1.5"),
    "logistics_effectiveness": Published("96.4%", "BRD v1.5"),
    "facilitator_performance": Published("100.0%", "BRD v1.5"),
    "participation_rate": Published(
        "60.4%", "BRD v1.5", "and 66.7% elsewhere in the same workbook"
    ),
}

HEADINGS: dict[Provenance, tuple[str, str]] = {
    Provenance.UNCHANGED: (
        "Unchanged definition",
        "The platform computes these the way the workbook did. Where the number still "
        "differs it is coverage, not method: the workbook counted 40 of the 50 sessions "
        "delivered in this window, so it was measuring the same thing over less of it. "
        "Showing these first establishes that the platform agrees with the workbook "
        "wherever the workbook was right.",
    ),
    Provenance.RESTATED: (
        "Restated",
        "Same intent, computed properly. The number moves because the old one was "
        "assembled by hand or keyed on the wrong thing — not because anything about "
        "the training changed.",
    ),
    Provenance.RENAMED: (
        "Renamed",
        "Same formula, a name that says what it measures.",
    ),
    Provenance.CORRECTED: (
        "Corrected",
        "The published figure was wrong. These are the difficult conversations, and "
        "each one has a defect number attached to it.",
    ),
    Provenance.NEW: (
        "New",
        "The workbook had no equivalent. Nothing to reconcile — but each needs its "
        "definition read once before anybody acts on it.",
    ),
}


def _row(value: MetricValue) -> str:
    published = WORKBOOK_FIGURES.get(value.key)
    workbook = published.value if published else "—"
    sample = f"{value.sample_size:,}" if value.sample_size else "—"
    return f"| {value.title} | {workbook} | **{value.formatted()}** | {sample} |"


def _section(provenance: Provenance, values: dict[str, MetricValue]) -> list[str]:
    heading, blurb = HEADINGS[provenance]
    metrics = [m for m in registry.METRICS if m.spec.provenance is provenance]
    if not metrics:
        return []

    lines = [f"### {heading}", "", blurb, ""]
    lines += ["| Metric | Workbook | Platform | n |", "|---|---|---|---|"]
    for metric in metrics:
        computed = values.get(metric.spec.key)
        if computed is not None:
            lines.append(_row(computed))
    lines.append("")

    for metric in metrics:
        spec = metric.spec
        computed = values.get(spec.key)
        # The definition and the population ship with every figure, because a
        # number read without its scope is a number somebody will put on a
        # slide without its scope — which is precisely P-03.
        detail = [f"**{spec.title}** — {spec.definition}", "", f"*Counts over:* {spec.population}."]
        if spec.population.excludes:
            detail.append("*Excludes:* " + "; ".join(spec.population.excludes) + ".")
        if spec.note:
            detail.append(f"*Why it changed:* {spec.note}")
        published = WORKBOOK_FIGURES.get(spec.key)
        if published is not None:
            source = f"*Workbook figure from {published.source}"
            source += f" — {published.note}.*" if published.note else ".*"
            detail.append(source)
        if computed is not None and not computed.is_defined:
            detail.append(
                "*Not computable yet.* No value is produced for this metric on the "
                "current data — see **What is still blocked** below."
            )
        lines += ["", *detail]

    return [*lines, ""]


#: Titles, because `blocked` is a list of titles. Kept beside the paragraph each
#: one triggers so a renamed metric fails the test rather than silently dropping
#: its explanation.
_SURVEY_TITLES = (
    "Knowledge Relevance",
    "Activity Effectiveness",
    "Logistics Effectiveness",
    "Facilitator Performance",
    "Net Promoter Score",
)
_LINKEDIN_TITLES = ("LinkedIn Hours", "Blended Learner Hours", "Unique Reach")


def _care(values: dict[str, MetricValue]) -> list[str]:
    """The two figures that will be misread, and the one that surprised us.

    Written out rather than left to the per-metric notes because both are
    misread in the same way — as a decline in performance — and the framing has
    to be decided before the meeting rather than improvised in it.
    """
    participation = values.get("participation_rate")
    learner_hours = values.get("learner_hours")

    lines = [
        "## The conversations that need care",
        "",
        "### Participation Rate: the largest restatement in the project",
        "",
        "It was wrong twice over. The denominator was the literal number 192 typed "
        "into a formula (P-01), and it then divided five companies' attendance by one "
        "company's headcount (P-13). Both halves now come from the same roster.",
        "",
    ]
    if participation is not None and participation.is_defined:
        lines += [
            f"Over the workbook's window it reads **{participation.formatted()}** — "
            f"{participation.numerator:,.0f} people who attended, over "
            f"{participation.denominator:,.0f} active employees the CRM can enroll.",
            "",
        ]
    lines += [
        "The old 60.4% and 66.7% are not larger versions of this number. They are a "
        "different measurement, and presenting them side by side invites the reading "
        "that participation collapsed. It did not: it was never measured.",
        "",
        "### NPS: two changes, and only one is a correction",
        "",
        "The scope was wrong — the published figure ran over a silently filtered 55 of "
        "77 responses (P-03). But the **unit** was also wrong: 92.7% is a percentage, "
        "and NPS is an index from -100 to +100.",
        "",
        "Shown without explanation, an NPS of +88 reads as a fall from 92.7. It is "
        "not, and nothing about satisfaction changed. Say: *NPS is now reported on the "
        "standard -100 to +100 scale.* Then give the number.",
        "",
    ]

    # The one nobody predicted, and the direction makes it worth raising early.
    if learner_hours is not None and learner_hours.is_defined:
        lines += [
            "### Learner Hours is lower than the workbook, and that needs an answer",
            "",
            f"Over the same window the platform reports **{learner_hours.formatted()}** "
            "against the workbook's 1,386.0. Every other figure moved upward, so this "
            "one will be asked about.",
            "",
            "It is the only figure here where the workbook is *higher* than the "
            "platform, and it is not explained by coverage. Learner Hours is session "
            "duration summed once per attendance, so the likely cause is the "
            "workbook's single `hours` column being read as both delivered hours and "
            "learner hours in different places. Confirm the cause before the meeting "
            "rather than in it.",
            "",
        ]
    return lines


def render(session: Session) -> str:
    """Build the statement from the registry and the computed values."""
    like_for_like = {v.key: v for v in registry.compute_all(session, WORKBOOK)}
    full = {v.key: v for v in registry.compute_all(session)}

    blocked = sorted(
        metric.spec.title
        for metric in registry.METRICS
        if (value := full.get(metric.spec.key)) is not None and not value.is_defined
    )

    lines = [
        "# Reconciliation statement — platform against workbook",
        "",
        "<!-- GENERATED by `python -m lnd.reference.reconcile`. Do not edit by hand:",
        "     every figure and every explanation comes from the metric registry, and a",
        "     hand-edit would drift from the code it is supposed to describe. -->",
        "",
        f"Generated {datetime.now(UTC).date().isoformat()} from the metric registry "
        f"against the frozen reference dataset.",
        "",
        "## The rule this is written under",
        "",
        "The training delivered did not change. Only the arithmetic describing it did.",
        "",
        "Every difference below has one of three causes, and it is worth naming which "
        "before showing anyone a number:",
        "",
        "- **Coverage** — the workbook covers February to August 2026. The platform "
        "holds July 2025 to September 2026. Most of the apparent growth is this.",
        "- **Grain** — the workbook counted the wrong thing: titles instead of program "
        "ids, a `#` column instead of a session id.",
        "- **A defect** — the figure was wrong on its own terms.",
        "",
        "A figure that rose because of coverage is not an improvement in performance, "
        "and a figure that fell because a defect was corrected is not a decline. Both "
        "will be read that way unless said out loud first.",
        "",
        "## The comparison window",
        "",
        "The **Platform** column below is computed over **1 February to 31 August "
        "2026** — the period the workbook covers. That is the only like-for-like "
        "comparison, and it is what should be presented.",
        "",
        "The full dataset is shown separately at the end. Comparing *that* against the "
        "workbook's column would attribute to correction what is mostly coverage.",
        "",
    ]

    for provenance in (
        Provenance.UNCHANGED,
        Provenance.RESTATED,
        Provenance.RENAMED,
        Provenance.CORRECTED,
        Provenance.NEW,
    ):
        lines += _section(provenance, like_for_like)

    lines += [
        "## The same figures over the full dataset",
        "",
        "July 2025 to September 2026. **Not comparable to the workbook column** — it "
        "covers roughly twice the period. This is what the platform will publish once "
        "it is live.",
        "",
        "| Metric | Feb-Aug 2026 | Full dataset | n (full) |",
        "|---|---|---|---|",
    ]
    for metric in registry.METRICS:
        key = metric.spec.key
        narrow, wide = like_for_like.get(key), full.get(key)
        if narrow is None or wide is None:
            continue
        sample = f"{wide.sample_size:,}" if wide.sample_size else "—"
        lines.append(
            f"| {metric.spec.title} | {narrow.formatted()} | {wide.formatted()} | {sample} |"
        )

    lines += [
        "",
        "## What is still blocked",
        "",
    ]
    if blocked:
        lines += [
            "These metrics produce no value on the current data:",
            "",
            *(f"- **{title}**" for title in blocked),
            "",
        ]
        # Said per cause, not as one paragraph covering whatever happens to be
        # blocked. The survey sentence was emitted unconditionally and stayed in
        # the document after the question map was seeded and the quality scores
        # started computing — a generated statement asserting a blockage that
        # had been cleared, in the one document whose whole purpose is to be
        # trusted in front of stakeholders.
        if any(title in blocked for title in _SURVEY_TITLES):
            lines += [
                "The quality metrics and NPS are blocked on one thing: "
                "`app.survey_question_map` has no row for the questions these "
                "programmes ask, so no answer can be attributed to a measured "
                "dimension. Every response is stored; none is scored. Until the map "
                "exists the stakeholder gate cannot be held on the figures L&D most "
                "wants to see.",
                "",
            ]
        if any(title in blocked for title in _LINKEDIN_TITLES):
            lines += [
                "LinkedIn Hours, Blended Learner Hours and Unique Reach have no source "
                "connected — the export's delivery mechanism and column set are still "
                "open with L&D. They report no value rather than zero: there has been "
                "no measurement, which is not a measurement of none.",
                "",
            ]
    else:
        lines += ["Nothing. Every metric produces a value.", ""]

    lines += _care(like_for_like)

    lines += [
        "## How to present this",
        "",
        "Bring the evidence, not the summary: the hardcoded 192, the two `Hard Talks` "
        "program ids, the two spellings of one trainer's name, and the pivot filter "
        "that drops the two largest programs from every quality score. Let the file "
        "make the argument.",
        "",
        "Lead with coverage, because it explains most of the movement and nobody "
        "disputes it. Then the restated figures. Then the corrections, each with its "
        "defect number. Save Participation Rate and NPS for last and give them room — "
        "see the notes on those two above.",
        "",
        "**Never present the workbook's 60.4% or 66.7% as comparable to the current "
        "participation rate.** They are not smaller versions of the same measurement; "
        "they are a different measurement.",
        "",
        "## Sign-off",
        "",
        "| | |",
        "|---|---|",
        "| Every restated figure walked through with L&D | ☐ |",
        "| Every corrected figure accepted, with its cause | ☐ |",
        "| Blocked metrics acknowledged as outstanding | ☐ |",
        "| L&D Director signature | |",
        "| Date | |",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate docs/reconciliation.md")
    parser.add_argument("--check", action="store_true", help="compare, write nothing")
    # Same flag, and the same reason, as the golden generator: this replays the
    # frozen dataset, and a replay into a database that already holds facts
    # doubles every grain. `replay` now refuses that outright, so without a
    # scratch database to point at, the refusal would be the whole experience.
    parser.add_argument(
        "--database-url",
        help="a scratch database to replay the frozen dataset into (defaults to DATABASE_URL)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.database_url:
        import os

        from lnd.config import get_settings
        from lnd.db import dispose_engine

        os.environ["DATABASE_URL"] = args.database_url
        get_settings.cache_clear()
        dispose_engine()

    from lnd.db import session_scope
    from lnd.reference.replay import replay

    with session_scope() as session:
        replay(session)
        statement = render(session)

    if args.check:
        current = STATEMENT.read_text(encoding="utf-8") if STATEMENT.exists() else ""
        # The generated-on date differs whenever this runs on another day, so
        # the comparison is over everything else.
        same = _without_date(current) == _without_date(statement)
        log.info("reconciliation statement %s", "matches" if same else "DIFFERS")
        return 0 if same else 1

    STATEMENT.parent.mkdir(parents=True, exist_ok=True)
    STATEMENT.write_text(statement, encoding="utf-8")
    log.info("wrote %s", STATEMENT)
    return 0


def _without_date(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith("Generated "))


if __name__ == "__main__":  # pragma: no cover - a script
    raise SystemExit(main())
