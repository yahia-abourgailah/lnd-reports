"""The week-4 gate: a change that moves a published figure fails the build.

The frozen dataset goes through the real pipeline — land, validate, shred,
resolve, conform, except — and every figure that comes out is compared against
`golden.json`. `core` is a pure function of (raw + enrichment), so the same
input must produce the same numbers forever. When it does not, one of two
things happened, and telling them apart is a conversation rather than a diff:

    somebody improved a definition   → update golden.json in the same PR
    somebody broke one               → fix the code

Both routes are deliberate. There is no way to make this pass by accident, and
nothing in the test suite can regenerate the golden file — that is a thing a
person does, in a commit, with a reviewer who looks at the number that moved.
Regenerate with:

    python -m lnd.reference.golden

WHY THE FAILURES ARE REPORTED AS A LIST

A bare `assert computed == golden` on a nested dict prints both dicts and
leaves somebody to diff two hundred numbers by eye at the moment they are least
inclined to. Each assertion below collects every figure that moved and names
them, so the failure says `attendance 1165 -> 1164` and stops there.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from lnd.reference import golden as golden_module
from lnd.reference.replay import replay

TRUNCATE = (
    "TRUNCATE raw.source_record, core.dim_date, core.dim_trainer, "
    "core.dim_employee, core.dim_program, core.dim_session, "
    "core.fact_enrollment, core.fact_attendance, core.fact_evaluation, "
    "app.enrichment_program_override, app.trainer_alias, "
    "app.survey_question_map, app.survey_option_score, "
    "app.identity_mapping, ops.dq_exception "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture(scope="module")
def replayed(db_engine: Engine) -> Iterator[Session]:
    """Replay the reference dataset once for the whole module.

    Module-scoped rather than per-test because the replay lands 57 program
    trees and 1,428 roster rows and runs a full transform — four seconds that
    would otherwise be paid by every assertion in the file. Nothing here
    writes, so sharing one post-transform state between the tests is safe, and
    the transaction is rolled back at the end so a dev database is left as it
    was found.
    """
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


@pytest.fixture(scope="module")
def computed(replayed: Session) -> dict[str, Any]:
    return golden_module.compute(replayed)


@pytest.fixture(scope="module")
def expected() -> dict[str, Any]:
    return golden_module.load()


def diff(computed: Any, expected: Any, prefix: str = "") -> list[str]:
    """Every leaf that differs, named by its path.

    Recursive because the golden file is nested two or three deep, and a
    failure that says `per_month.2026-03.attendance 41 -> 38` locates itself
    while `two dicts are not equal` does not.
    """
    moved: list[str] = []
    if isinstance(expected, dict) and isinstance(computed, dict):
        for key in sorted(set(expected) | set(computed)):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in computed:
                moved.append(f"{path}: {expected[key]!r} -> missing")
            elif key not in expected:
                moved.append(f"{path}: new -> {computed[key]!r}")
            else:
                moved.extend(diff(computed[key], expected[key], path))
    elif computed != expected:
        moved.append(f"{prefix}: {expected!r} -> {computed!r}")
    return moved


def assert_unchanged(section: str, computed: dict[str, Any], expected: dict[str, Any]) -> None:
    moved = diff(computed.get(section), expected.get(section), section)
    if moved:
        pytest.fail(
            f"{len(moved)} published figure(s) moved in `{section}`.\n\n"
            + "\n".join(f"  {line}" for line in moved[:40])
            + (f"\n  ... and {len(moved) - 40} more" if len(moved) > 40 else "")
            + "\n\nIf the change is intended, regenerate with "
            "`python -m lnd.reference.golden` in the same pull request, and say "
            "in the description which figure moved and why.",
            pytrace=False,
        )


pytestmark = pytest.mark.usefixtures("db_engine")


class TestGoldenValues:
    def test_totals_have_not_moved(
        self, computed: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        """The headline figures, including both hour metrics.

        Training Hours Delivered and Learner Hours are pinned separately
        because the workbook tracked both under one name and quoted them
        interchangeably — 130.5 and 1,386 for the same month.
        """
        assert_unchanged("totals", computed, expected)

    def test_per_program_figures_have_not_moved(
        self, computed: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        """A join that attaches rows to the wrong program leaves every total
        untouched. This is what notices."""
        assert_unchanged("per_program", computed, expected)

    def test_monthly_figures_have_not_moved(
        self, computed: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        """Every trend on the dashboard groups by month, and a date handled
        twice moves rows across a boundary without changing a total."""
        assert_unchanged("per_month", computed, expected)

    def test_breakdowns_have_not_moved(
        self, computed: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        """Where a conforming failure shows up. P-05 split one sector into two
        and every total held still while it did."""
        assert_unchanged("breakdowns", computed, expected)

    def test_identity_resolution_has_not_moved(
        self, computed: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        """A resolution regression is invisible in the totals — the rows still
        exist — and appears here as attendance sliding into `unresolved`."""
        assert_unchanged("identity", computed, expected)

    def test_the_exception_census_has_not_moved(
        self, computed: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        """A rule that stops firing looks exactly like the data improving."""
        assert_unchanged("exceptions", computed, expected)


class TestTheMetricsHaveNotMoved:
    """The twenty-one KPIs, under both comparison windows.

    Grain counts alone are not enough and neither are KPIs alone. Two errors at
    different grains can cancel inside a ratio and leave the percentage
    untouched, so both halves of every ratio are pinned as well as the quotient
    — a numerator and a denominator that move together produce a figure that
    does not.
    """

    def test_no_published_figure_moved(
        self, computed: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        assert_unchanged("metrics", computed, expected)

    def test_every_registered_metric_is_pinned(self, computed: dict[str, Any]) -> None:
        """A metric added to the registry without a golden value would ship
        unpinned, which is the one gap this suite exists to close."""
        from lnd.metrics import registry

        pinned = set(computed["metrics"]["full_dataset"])
        registered = {metric.spec.key for metric in registry.METRICS}
        assert registered - pinned == set(), (
            f"metrics in the registry with no golden value: "
            f"{sorted(registered - pinned)}. Run `python -m lnd.reference.golden`."
        )

    def test_the_blocked_metrics_are_blocked_for_a_stated_reason(
        self, computed: dict[str, Any]
    ) -> None:
        """Six metrics return nothing today, and it must stay deliberate.

        Five quality scores and NPS are blocked on `app.survey_question_map`
        being empty; the three LinkedIn metrics have no source connected. A
        metric that started producing a number without anybody authoring that
        map would mean it had begun guessing, which is worse than returning
        nothing.
        """
        values = computed["metrics"]["full_dataset"]
        undefined = {key for key, v in values.items() if v["value"] is None}
        assert undefined == {
            "knowledge_relevance",
            "activity_effectiveness",
            "logistics_effectiveness",
            "facilitator_performance",
            "nps",
            "linkedin_hours",
            "blended_learner_hours",
            "unique_reach",
        }


class TestHistory:
    """Week 4 task 1, asserted rather than remembered."""

    def test_history_starts_before_the_plan_says_it_does(self, computed: dict[str, Any]) -> None:
        """Q-10 records history as beginning September 2025. It begins in July,
        and 68 of 123 sessions fall in 2025 — too many to wave through."""
        from lnd.reference.windows import EXPECTED_HISTORY_START

        assert computed["history"]["first_session"] == EXPECTED_HISTORY_START.isoformat()
        assert computed["history"]["sessions_by_year"]["2025"] == 68

    def test_every_session_has_a_time(self, computed: dict[str, Any]) -> None:
        """Which is what makes Training Hours Delivered fully derivable rather
        than partly estimated."""
        assert computed["history"]["sessions_without_a_time"] == 0

    def test_the_history_range_has_not_moved(
        self, computed: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        assert_unchanged("history", computed, expected)


class TestTheDefectsStayFixed:
    """The workbook's defects, measured. If the platform ever starts agreeing
    with the workbook again, these move."""

    def test_the_defect_measurements_have_not_moved(
        self, computed: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        assert_unchanged("defects", computed, expected)

    def test_title_grouping_would_still_lose_programs(self, computed: dict[str, Any]) -> None:
        """P-02, and the reason `dim_program` is keyed on the CRM id.

        Sixteen of 57 programs share a title with another. Excel grouped by
        title, so sixteen of them vanished into a neighbour's row taking their
        attendance and their scores along.
        """
        assert computed["defects"]["programs_lost_to_title_grouping"] == 16

    def test_one_trainer_is_still_spelled_two_ways(self, computed: dict[str, Any]) -> None:
        """P-04. Sixteen raw spellings resolve to fifteen people, and the
        dataset preserves the pair rather than tidying it away."""
        assert computed["defects"]["trainer_name_variants"] == 16
        assert computed["defects"]["trainers_after_merge"] == 15

    def test_no_attendance_is_left_unresolved(self, computed: dict[str, Any]) -> None:
        """P-07. The workbook dropped 38 attendees silently; the claim being
        pinned is that this platform drops none."""
        assert computed["defects"]["unresolved_attendance"] == 0

    def test_the_denominator_still_spans_five_companies(self, computed: dict[str, Any]) -> None:
        """P-13. The workbook divided attendees drawn from several companies by
        the employees of one."""
        assert computed["defects"]["companies_among_employees"] == 5

    def test_counting_on_status_would_still_be_wrong(self, computed: dict[str, Any]) -> None:
        """`status` and `computed_status` disagree on nearly every program.

        Total Programs counted off `status` yields nothing at all; the real
        answer is 55. Only `computed_status` is trustworthy.
        """
        assert computed["totals"]["programs_completed"] == 55
        assert computed["totals"]["programs_status_says_completed"] == 0


class TestTheDatasetItself:
    def test_it_carries_no_real_people(self) -> None:
        """The freezer refuses to write a leaky dataset, and this states the
        property the repository depends on rather than leaving it to a script
        nobody runs.

        A substring scan cannot prove a negative here — the real names are not
        available to the test suite, which is the point of anonymising — so it
        checks the shape instead: every email is in the reserved `.test`
        domain, which no substituted value can escape and no real one can be.
        """
        data = golden_module.__dict__  # noqa: F841 - placeholder for clarity
        from lnd.reference.freeze import load as load_dataset

        dataset = load_dataset()
        emails = [
            user["email"]
            for entry in (
                [e for program in dataset["programs"] for e in program["users"]]
                + [{"user": u} for u in dataset["roster"]]
            )
            if isinstance(user := entry.get("user"), dict) and user.get("email")
        ]
        assert emails, "the dataset should carry emails, substituted ones"
        assert all(email.endswith("@example.test") for email in emails)

    def test_the_defects_survived_anonymisation(self) -> None:
        """A reference dataset that cleaned up the data would prove nothing.

        P-05 is the one that is easiest to destroy by accident: trimming on the
        way into the fixture would leave the pipeline's conforming step with
        nothing to do and the test suite asserting a defect nobody has.
        """
        from lnd.reference.freeze import load as load_dataset

        dataset = load_dataset()
        sectors = [
            user["sector"]
            for program in dataset["programs"]
            for entry in program["users"]
            if isinstance(user := entry.get("user"), dict) and user.get("sector")
        ]
        untrimmed = [sector for sector in sectors if sector != sector.strip()]
        assert untrimmed, "P-05's trailing spaces must survive into the fixture"
        assert len({s.strip() for s in sectors}) < len(set(sectors))
