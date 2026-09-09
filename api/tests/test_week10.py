"""Week 10: the parallel run, the security review, the restore and the report.

Four generators that produce the documents the launch gate turns on. Each of
them can *fail*, and what is tested here is mostly that: a document that reports
a clean result whatever it finds is a document nobody should sign.

The heavy ones run against a replayed reference dataset, module-scoped for the
same reason `test_reference.py` does it — the replay lands 57 program trees and
runs a full transform, and paying four seconds per assertion would make this
file the slowest in the suite for no benefit.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from lnd.export import compare as report_compare
from lnd.metrics import registry
from lnd.metrics.base import Provenance
from lnd.reference import parallel, perf, restore, security
from lnd.reference.replay import replay
from lnd.reference.windows import WORKBOOK

from .test_reference import TRUNCATE


@pytest.fixture(scope="module")
def replayed(db_engine: Engine) -> Iterator[Session]:
    connection = db_engine.connect()
    transaction = connection.begin()
    connection.execute(text(TRUNCATE))

    session = Session(bind=connection, expire_on_commit=False)
    replay(session)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# --------------------------------------------------------------- parallel run


def test_a_difference_with_no_cause_is_unexplained() -> None:
    """The property the whole document rests on.

    Everything else in `parallel.py` is presentation. This is the assertion
    that makes "every difference has a written cause" a gate rather than a
    sentence in a report.
    """
    without = parallel.Difference(
        key="k",
        title="T",
        workbook="1",
        reference="2",
        live="2",
        moved_from_workbook=True,
        moved_from_reference=False,
        cause="",
    )
    assert without.unexplained

    assert not parallel.Difference(**{**vars(without), "cause": "because"}).unexplained
    # A figure that did not move needs no cause, and must not be reported as
    # missing one — otherwise the failure list is every unchanged metric.
    assert not parallel.Difference(**{**vars(without), "moved_from_workbook": False}).unexplained


def test_every_moved_metric_carries_its_own_reason() -> None:
    """A restated or corrected metric with an empty `note` fails the run.

    Checked against the registry rather than against a rendered document,
    because this is where the cause has to live: the reconciliation statement,
    the parallel run and the dashboard tooltip all read this one string, and an
    empty one would print a heading with nothing under it in three places.
    """
    missing = [
        metric.spec.key
        for metric in registry.METRICS
        if metric.spec.provenance in (Provenance.RESTATED, Provenance.RENAMED, Provenance.CORRECTED)
        and not metric.spec.note.strip()
    ]
    assert not missing, f"no written cause for: {', '.join(missing)}"


def test_the_parallel_run_names_what_it_cannot_explain(replayed: Session) -> None:
    document, differences = parallel.render(replayed)

    assert differences, "compared nothing"
    assert "## Unexplained differences" in document
    for difference in differences:
        if difference.moved_from_workbook or difference.moved_from_reference:
            assert difference.cause or difference.unexplained

    # Against the frozen dataset the live column *is* the reference column, so
    # nothing may differ from it. A failure here means the golden file and the
    # replay disagree, which is the same fault the week-4 gate exists for.
    stale = [d.title for d in differences if d.moved_from_reference]
    assert not stale, f"differs from the pinned reference: {', '.join(stale)}"


def test_the_workbook_figures_are_not_recomputed() -> None:
    """The eleven published figures are transcribed, and carry their source.

    Nothing computes them — there is no machine-readable copy of the workbook's
    output — so the guard is that each one says where it came from.
    """
    from lnd.reference.reconcile import WORKBOOK_FIGURES

    assert all(published.source for published in WORKBOOK_FIGURES.values())


def test_a_replay_refuses_a_populated_database(replayed: Session) -> None:
    """The guard that would have caught both incidents at the first line.

    `replayed` has already run one, so `core` holds facts. A second must refuse
    rather than land the frozen payloads on top and double every grain. The
    transform invariant catches that too, but only after the landing, and only
    because of an ordering accident the first time it happened.
    """
    from lnd.reference.replay import DatabaseNotEmpty

    with pytest.raises(DatabaseNotEmpty, match="already holds"):
        replay(replayed)


# ------------------------------------------------------------ security review


def test_the_forbidden_fields_are_absent_from_the_source() -> None:
    """NFR-06, measured rather than remembered.

    The frozen dataset is anonymised by replacing values and keeping keys, so
    this is a statement about the CRM's schema. If the source ever starts
    sending a national ID, this fails before anybody decides whether to store
    it.
    """
    counts = security._forbidden_in_payloads()
    offending = {field: count for field, count in counts.items() if count}
    assert not offending, f"the source now exposes: {offending}"


def test_nothing_forbidden_is_stored() -> None:
    stored = {name for name, _, _ in security._stored_fields()}
    assert not stored & set(security.FORBIDDEN_FIELDS)


def test_there_is_no_crm_write_client() -> None:
    """Read-only at source, checked in the code rather than promised in a doc."""
    assert security._write_client_present() == []


def test_the_review_lists_what_the_source_offers_and_we_refuse() -> None:
    """Minimisation is evidence, not a claim.

    `mobile` is the field the CRM sends and the platform declines. If the
    rejected set ever empties, either somebody started storing everything or
    the comparison stopped working — and both should be noticed here.
    """
    assert security._rejected_fields()


def test_a_writable_raw_layer_is_a_finding(replayed: Session) -> None:
    """The review must be able to fail.

    Asserted by rendering it against a session whose role *does* hold UPDATE on
    raw — the dev owner — rather than trusting that the finding logic is right
    because the happy path is green.
    """
    document, findings = security.render(replayed)
    assert "## Sign-off" in document
    # The test database connects as the owning role, which holds every
    # privilege. So this must report findings; a clean pass here would mean the
    # grant check is not looking at anything.
    assert any("append-only" in finding for finding in findings)


# --------------------------------------------------------------- the restore


def test_the_restore_expects_raw_to_stay_append_only() -> None:
    assert restore.EXPECTED_RAW_GRANTS["INSERT"] is True
    assert restore.EXPECTED_RAW_GRANTS["UPDATE"] is False
    assert restore.EXPECTED_RAW_GRANTS["DELETE"] is False


def test_the_restore_report_marks_a_mismatch(replayed: Session) -> None:
    """A grain that did not survive must render as a failure, not a row.

    The rehearsal passed on the day it was run, which proves the happy path and
    nothing else. This is the other half.
    """
    census = restore._census(replayed)
    short = {**census, "attendance": census["attendance"] - 1}
    document = restore.render(
        dump="d.dump",
        target="scratch",
        source_census=census,
        restored_census=short,
        source_figures={"Learner Hours": "1,153.0 hours"},
        restored_figures={"Learner Hours": "1,100.0 hours"},
        grants={"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": False, "TRUNCATE": False},
        seconds=1.0,
        findings=["attendance short by one"],
    )
    assert "did not pass" in document
    assert "**✗**" in document


# ---------------------------------------------------------- report comparison


def test_every_headline_figure_lands_in_its_own_cell(replayed: Session) -> None:
    """The check week 8 could not make by eye.

    A correct metric written into the wrong column produces a file that is
    right everywhere except where a reader looks, and no test above the
    spreadsheet notices.
    """
    rows = report_compare.compare(replayed, WORKBOOK)
    misplaced = [row.label for row in rows if not row.lands_correctly]
    assert not misplaced, f"wrong cell: {', '.join(misplaced)}"
    assert len(rows) == len(report_compare.PLACEMENTS) + 1


def test_the_comparison_catches_a_swapped_cell(replayed: Session) -> None:
    """`_holds` must be strict about the number and lenient about rounding.

    Both halves matter. Lenient on the value and a swap goes unnoticed; strict
    on the rendering and every hours figure fails on `Decimal` rounding half to
    even where a float does not.
    """
    knowledge = registry.compute("knowledge_relevance", replayed, WORKBOOK)
    logistics = registry.compute("logistics_effectiveness", replayed, WORKBOOK)

    assert report_compare._holds(float(knowledge.value or 0) / 100, knowledge)
    assert not report_compare._holds(float(logistics.value or 0) / 100, knowledge)

    hours = registry.compute("training_hours_delivered", replayed, WORKBOOK)
    assert report_compare._holds(float(hours.value or 0), hours)


# ------------------------------------------------------------- perf harness


def test_the_perf_gate_is_the_brd_ceiling() -> None:
    assert perf.GATE_MS == 2000.0
    assert perf.TARGET_ATTENDANCE == 15_000


def test_a_view_that_does_not_return_200_fails_the_gate() -> None:
    """A 404 at four milliseconds is not a passing view.

    It was, in the first run: three URLs were wrong and reported as failures
    only because the status was checked as well as the time. Without that they
    would have been the three fastest rows in the table.
    """
    fast_but_broken = perf.Timing(name="n", path="/p", status=404, cold_ms=[1.0], warm_ms=[1.0])
    assert not fast_but_broken.passes

    slow = perf.Timing(name="n", path="/p", status=200, cold_ms=[9_000.0], warm_ms=[1.0])
    assert not slow.passes

    fine = perf.Timing(name="n", path="/p", status=200, cold_ms=[120.0], warm_ms=[30.0])
    assert fine.passes


def test_the_percentile_is_an_observation() -> None:
    """Not an interpolation. With twenty samples the p95 is the 19th."""
    timing = perf.Timing(
        name="n", path="/p", status=200, cold_ms=[float(n) for n in range(1, 21)], warm_ms=[]
    )
    assert timing.cold_p95 == 19.0
    assert timing.worst == 20.0


def test_the_clone_never_amplifies_its_own_clones() -> None:
    """Growth must be linear in the number of passes, not exponential.

    The `WHERE crm_program_id < CLONE_OFFSET` predicate is the whole of it:
    without it the second pass copies the first pass's copies and the run
    overshoots 15,000 by a factor nobody chose.
    """
    import inspect

    source = inspect.getsource(perf.amplify)
    assert '"crm_program_id" < {CLONE_OFFSET}' in source
