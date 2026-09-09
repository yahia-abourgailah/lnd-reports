"""The parallel run: the live platform against the workbook, for a real cycle.

    python -m lnd.reference.parallel            # writes docs/parallel-run.md
    python -m lnd.reference.parallel --check    # writes nothing; fails on an
                                                # unexplained difference

Week 10, and the gate everything else waits behind. The reconciliation
statement is generated against the *frozen* dataset, which is what makes it
stable enough to pin in CI — and is exactly why it cannot be the document shown
to the Director on its own. A frozen dataset says what the platform computed on
the day somebody froze it. The parallel run says what it computes now, on the
live database, after a real sync and a real transform.

WHAT THIS PROVES THAT THE RECONCILIATION CANNOT

Three claims, and the third is the one that matters.

1. The live platform agrees with the reconciliation statement. Where it does
   not, the statement is stale — and *regenerating it changes nothing*, because
   it is generated from the frozen dataset. The remedy is to re-freeze, with the
   golden file updated in the same reviewed commit, or to present the live
   figure and say why it differs. Either is a decision; neither is a rerun.
2. Every difference from the workbook carries a written cause, taken from the
   metric's own declaration rather than from anybody's memory of the meeting.
3. **Every difference that has no cause is named and the run fails.** That is
   the difference between "we explained the differences" as a promise and as a
   property. A parallel run that cannot fail is a document, not a gate.

WHY A DIFFERENCE FROM THE FROZEN FIGURE IS NOT AUTOMATICALLY A FAULT

The source moves. Employees join and leave, sessions are delivered, surveys are
answered — between the freeze and the run, the roster alone will have shifted.
So a moved figure is attributed to a moved grain: if the live census differs
from the frozen one, the movement has a cause and is reported with it. If every
grain is identical and a figure still moved, nothing about the data explains it
and the only remaining explanation is the code, which is a failing run.

WHY IT DOES NOT REPLAY

`golden` and `reconcile` both call `replay()` first, which lands the frozen
payloads into whatever database they are pointed at. This one deliberately does
not: it reads the live `core` exactly as the dashboard does, because a parallel
run against a reconstructed database would be a rehearsal of the rehearsal.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lnd.metrics import registry
from lnd.metrics.base import MetricValue, Provenance
from lnd.reference import golden
from lnd.reference.reconcile import WORKBOOK_FIGURES
from lnd.reference.windows import WORKBOOK

log = logging.getLogger(__name__)

REPORT = Path(__file__).resolve().parents[4] / "docs" / "parallel-run.md"

#: Sessions the workbook's own dashboard counted over February-August 2026. The
#: platform sees 50 in the same window, and that gap is the whole of the
#: "coverage" cause — quoted here so an unchanged-definition metric that moved
#: can point at the evidence rather than asserting it.
WORKBOOK_SESSIONS_IN_WINDOW = 40


@dataclass(frozen=True)
class Difference:
    """One figure, three readings of it, and why they are not the same.

    `cause` empty means nothing in the registry or the census explains the
    movement, which is what `unexplained` reports and what fails the run.
    """

    key: str
    title: str
    workbook: str
    reference: str
    live: str
    moved_from_workbook: bool
    moved_from_reference: bool
    cause: str

    @property
    def unexplained(self) -> bool:
        return (self.moved_from_workbook or self.moved_from_reference) and not self.cause


def _reference_figures() -> dict[str, str]:
    """The workbook-window column of the reconciliation, as it was pinned.

    Read out of `golden.json` rather than recomputed, because the point is to
    compare the live platform against the document that will be signed — and
    the document was generated from these values.
    """
    values = golden.load()
    window = values.get("metrics", {}).get("workbook_window", {})
    return {key: str(entry.get("formatted", "—")) for key, entry in window.items()}


def _census(session: Session) -> dict[str, int]:
    """The grain counts, live. What attributes a moved figure to moved data.

    Deliberately the same grains the golden file pins under `totals`: a figure
    can only move because the rows underneath it moved, and if none of these
    changed then the rows did not.
    """
    live = golden._totals(session)
    return {key: value for key, value in live.items() if isinstance(value, int)}


def _moved_grains(session: Session) -> dict[str, tuple[Any, Any]]:
    frozen = golden.load().get("totals", {})
    live = golden._totals(session)
    return {
        key: (frozen.get(key), value)
        for key, value in live.items()
        if key in frozen and frozen[key] != value
    }


def _workbook_cause(value: MetricValue, sessions_in_window: int) -> str:
    """Why the live figure is not the workbook's, in the registry's own words."""
    provenance = value.provenance
    if provenance is Provenance.NEW:
        return "New. The workbook had no equivalent figure, so there is nothing to reconcile."
    spec = registry.get(value.key).spec
    if provenance is Provenance.UNCHANGED:
        return (
            "Coverage, not method. The definition is the workbook's; it was computed over "
            f"{WORKBOOK_SESSIONS_IN_WINDOW} of the {sessions_in_window} sessions delivered "
            "in this window."
        )
    # Restated, renamed and corrected all carry their reason on the metric. An
    # empty note here is a registry fault, not a data one, and returning ""
    # makes the run fail rather than printing a heading with nothing under it.
    return spec.note.strip()


def _reference_cause(moved: dict[str, tuple[Any, Any]]) -> str:
    if not moved:
        return ""
    grains = ", ".join(
        f"{key.replace('_', ' ')} {before:,} → {after:,}" for key, (before, after) in moved.items()
    )
    return f"The source has moved since the dataset was frozen: {grains}."


def compare(session: Session) -> list[Difference]:
    """Every metric, over the workbook's window, three ways."""
    reference = _reference_figures()
    moved = _moved_grains(session)
    reference_cause = _reference_cause(moved)

    sessions_in_window = _sessions_in_window(session)

    differences: list[Difference] = []
    for value in registry.compute_all(session, WORKBOOK):
        published = WORKBOOK_FIGURES.get(value.key)
        workbook = published.value if published else "—"
        live = value.formatted()
        frozen = reference.get(value.key, "—")

        moved_from_workbook = published is not None and workbook != live
        moved_from_reference = frozen != live

        causes = []
        if moved_from_workbook:
            causes.append(_workbook_cause(value, sessions_in_window))
        if moved_from_reference:
            causes.append(reference_cause)
        # A blank in either position is what `unexplained` reads: joining first
        # would let a real cause paper over a missing one.
        cause = "" if any(not c for c in causes) else " ".join(causes)

        differences.append(
            Difference(
                key=value.key,
                title=value.title,
                workbook=workbook,
                reference=frozen,
                live=live,
                moved_from_workbook=moved_from_workbook,
                moved_from_reference=moved_from_reference,
                cause=cause,
            )
        )
    return differences


def _sessions_in_window(session: Session) -> int:
    """Sessions the platform holds in the workbook's own window.

    The evidence behind the coverage cause. Quoted rather than asserted: an
    unchanged-definition metric that moved should be able to point at the
    sessions the workbook did not see, and 40 against this number is that.
    """
    from lnd.models.core import DimSession

    return (
        session.scalar(
            select(func.count())
            .select_from(DimSession)
            .where(DimSession.session_date >= WORKBOOK.date_from)
            .where(DimSession.session_date <= WORKBOOK.date_to)
            .where(DimSession.deleted_at_source.is_(None))
        )
        or 0
    )


def _cycle(session: Session) -> list[str]:
    """What the platform did on its own while this was being watched.

    A parallel run is a claim about a *cycle*, not about one query: the schedule
    fetched, the transform rebuilt, and the figures below came out of what it
    left behind. Read from `ops.sync_run` so the evidence is the platform's own
    audit rather than a sentence somebody typed.
    """
    from lnd.models.ops import SyncRun

    rows = session.execute(
        select(
            SyncRun.entity,
            SyncRun.status,
            func.count(),
            func.max(SyncRun.finished_at),
        )
        .group_by(SyncRun.entity, SyncRun.status)
        .order_by(SyncRun.entity)
    ).all()
    if not rows:
        return ["No sync has been recorded on this database.", ""]

    lines = [
        "| Entity | Outcome | Runs | Last |",
        "|---|---|---|---|",
    ]
    for entity, status, count, last in rows:
        when = last.strftime("%Y-%m-%d %H:%M") if last else "—"
        lines.append(f"| `{entity}` | {status} | {count} | {when} |")
    return [*lines, ""]


def render(session: Session) -> tuple[str, list[Difference]]:
    differences = compare(session)
    unexplained = [d for d in differences if d.unexplained]
    moved = _moved_grains(session)

    lines = [
        "# Parallel run — the live platform against the workbook",
        "",
        "<!-- GENERATED by `python -m lnd.reference.parallel`. Do not edit by hand. -->",
        "",
        f"Run {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} UTC against the **live** "
        "database, over 1 February to 31 August 2026 — the window the workbook covers.",
        "",
        "## What this is, and what it is not",
        "",
        "[`reconciliation.md`](reconciliation.md) is generated against the frozen "
        "reference dataset, which is what lets CI pin it. This one is generated against "
        "the live database after a real sync and a real transform, and it answers a "
        "question the frozen document cannot: *is the statement we are about to sign "
        "still true of the data we actually hold?*",
        "",
        "Every difference below carries a written cause, taken from the metric's own "
        "declaration. **A difference with no cause fails this run** — that is the "
        "difference between having explained the differences and being able to prove it.",
        "",
        "## The cycle",
        "",
        *_cycle(session),
        "## Every figure, three ways",
        "",
        "**Workbook** is what was published. **Reference** is the figure in the signed "
        "reconciliation statement, computed on the frozen dataset. **Live** is what the "
        "platform computes now. Workbook to Reference is the conversation with L&D; "
        "Reference to Live is whether that conversation is still current.",
        "",
        "| Metric | Workbook | Reference | Live | Moved |",
        "|---|---|---|---|---|",
    ]
    for d in differences:
        marks = []
        if d.moved_from_workbook:
            marks.append("vs workbook")
        if d.moved_from_reference:
            marks.append("**vs reference**")
        lines.append(
            f"| {d.title} | {d.workbook} | {d.reference} | **{d.live}** | "
            f"{', '.join(marks) if marks else '—'} |"
        )

    lines += ["", "## The cause of every difference", ""]
    for d in differences:
        if not (d.moved_from_workbook or d.moved_from_reference):
            continue
        lines += [f"**{d.title}** — {d.cause or '**NO WRITTEN CAUSE.** See below.'}", ""]

    lines += ["## Has the source moved since the freeze?", ""]
    if moved:
        lines += [
            "Yes, and this is the cause attributed to every figure that differs from the "
            "reference column. The grains that moved:",
            "",
            "| Grain | Frozen | Live |",
            "|---|---|---|",
            *(
                f"| {key.replace('_', ' ')} | {before:,} | {after:,} |"
                for key, (before, after) in moved.items()
            ),
            "",
            "A figure computed over a roster that has gained or lost people is *expected* "
            "to move. What would not be expected — and what fails this run — is a figure "
            "moving while every grain underneath it held still.",
            "",
            "**Regenerating `reconciliation.md` will not close this gap.** That document is "
            "generated from the frozen dataset, which is exactly what makes it stable "
            "enough to pin in CI. Closing the gap means re-freezing, with `golden.json` "
            "updated in the same reviewed commit — or presenting the live figure and "
            "saying why it differs from the signed one. Both are decisions somebody makes; "
            "neither is a command somebody reruns.",
            "",
        ]
    else:
        lines += [
            "No. Every grain is identical to the frozen dataset, so any figure differing "
            "from the reference column would have no explanation in the data at all.",
            "",
        ]

    lines += ["## Unexplained differences", ""]
    if unexplained:
        lines += [
            "**This run does not pass.** These figures moved and nothing in the registry "
            "or the census explains why. Each one must be given a cause — in the metric's "
            "`note`, or by regenerating the reference — before the sign-off meeting:",
            "",
            *(
                f"- **{d.title}**: workbook {d.workbook}, reference {d.reference}, live {d.live}"
                for d in unexplained
            ),
            "",
        ]
    else:
        lines += [
            "None. Every figure that differs from the workbook or from the reference "
            "statement has a written cause above.",
            "",
        ]

    lines += [
        "## What this run does not prove",
        "",
        "That the causes are *right*. It proves each one exists, is attached to the "
        "figure it explains, and came from the same declaration the dashboard and the "
        "exports read. Whether the workbook's 1,386.0 learner-hours came from reading "
        "one `hours` column two ways is a question for the meeting, and the "
        "reconciliation statement says so.",
        "",
        "It also does not prove the *workbook* column. Those eleven figures are "
        "transcribed by hand from the file and the BRD, and carry their source in "
        "`reconcile.py`.",
        "",
    ]
    return "\n".join(lines), differences


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate docs/parallel-run.md")
    parser.add_argument(
        "--check", action="store_true", help="write nothing; fail on an unexplained difference"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from lnd.db import session_scope

    with session_scope() as session:
        report, differences = render(session)

    unexplained = [d for d in differences if d.unexplained]
    stale = [d for d in differences if d.moved_from_reference]

    if not args.check:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(report, encoding="utf-8")
        log.info("wrote %s", REPORT)

    log.info("%d metrics compared", len(differences))
    if stale:
        log.info(
            "%d differ from the signed reference: %s",
            len(stale),
            ", ".join(d.title for d in stale),
        )
    for d in unexplained:
        log.error(
            "UNEXPLAINED %s: workbook %s, reference %s, live %s",
            d.title,
            d.workbook,
            d.reference,
            d.live,
        )
    return 1 if unexplained else 0


if __name__ == "__main__":  # pragma: no cover - a script
    raise SystemExit(main())
