"""Every published figure, computed once, so a change that moves one is loud.

    python -m lnd.reference.golden          # rewrite tests/reference/golden.json
    pytest tests/test_reference.py          # fail if anything moved

The week-4 gate. `core` is a pure function of (raw + enrichment), so running
the transform over a frozen input must produce the same numbers forever. When
it does not, either somebody improved a definition or somebody broke one, and
the difference between those two is a conversation rather than a diff — which
is the point. The golden file has to be updated in the same pull request as the
change that moved it, so a reviewer sees the number and agrees with it before
it reaches anyone who might publish it.

WHY THE FIGURES ARE COMPUTED FROM `core` AND NOT FROM THE PAYLOAD

Counting the payload would test the frozen file against itself. Every figure
here is read back out of the star schema after a real transform, so the shred
into three grains, identity resolution, SCD versioning, conforming and the
exception rules are all inside the thing being pinned. A regression anywhere in
that path moves a number here.

WHAT IS PINNED, AND WHY EACH ONE

Totals catch a grain collapsing. Per-program and per-month figures catch a
regression that keeps the total right while moving rows between buckets —
which is what a broken join usually does. Breakdowns by company, sector and
job level catch conforming failures: P-05 was a trailing space inventing five
sectors that do not exist, and it would show here as a sector count moving
without any total moving. The exception census catches a rule that silently
stops firing, which would otherwise look like the data improving.

DECIMALS ARE STRINGS

`Decimal("130.50")` and `130.5` are the same quantity and different JSON. Hours
are serialised as strings so that a figure changing in the second decimal place
is a diff rather than a float comparison nobody trusts.
"""

from __future__ import annotations

import argparse
import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lnd.models.core import (
    DimEmployee,
    DimProgram,
    DimSession,
    DimTrainer,
    FactAttendance,
    FactEnrollment,
    FactEvaluation,
)
from lnd.models.ops import DqException, DqStatus

log = logging.getLogger(__name__)

GOLDEN = Path(__file__).resolve().parents[3] / "tests" / "reference" / "golden.json"


def _money(value: Decimal | int | float | None) -> str | None:
    """A quantity as a string, so 130.5 and 130.50 are one value."""
    return None if value is None else str(Decimal(str(value)).quantize(Decimal("0.01")))


def _pairs(session: Session, statement: Any) -> dict[str, int]:
    """A two-column query as a JSON-safe, key-sorted mapping.

    Sorted because a golden file whose key order depends on how PostgreSQL felt
    that morning produces a diff on every run and teaches everyone to ignore
    diffs.
    """
    return {
        ("(none)" if key is None else str(key)): count
        for key, count in sorted(
            session.execute(statement).all(), key=lambda row: (row[0] is None, str(row[0]))
        )
    }


def compute(session: Session) -> dict[str, Any]:
    """Read every pinned figure out of `core`, `ops` and the metric registry."""
    return {
        "totals": _totals(session),
        "per_program": _per_program(session),
        "per_month": _per_month(session),
        "breakdowns": _breakdowns(session),
        "identity": _identity(session),
        "exceptions": _exceptions(session),
        "defects": _defects(session),
        "metrics": _metrics(session),
        "history": _history(session),
    }


def _metrics(session: Session) -> dict[str, dict[str, Any]]:
    """All twenty-one KPIs, under each comparison window.

    The KPIs alone would not be enough, which is why the grain counts above
    exist too: two errors at different grains can cancel inside a ratio and
    leave it looking untouched. Both halves of every ratio are pinned
    separately for the same reason — a numerator and a denominator that move
    together produce a percentage that does not.

    `sample_size` is pinned as well. A quality score over 297 responses and the
    same score over 4 are different claims, and a regression that quietly
    shrinks the population being measured would otherwise be invisible.
    """
    from lnd.metrics import registry
    from lnd.reference.windows import WINDOWS

    windows: dict[str, dict[str, Any]] = {}
    for name, filters in WINDOWS:
        values: dict[str, Any] = {}
        for computed in registry.compute_all(session, filters):
            values[computed.key] = {
                "value": None if computed.value is None else _money(computed.value),
                "numerator": _money(computed.numerator),
                "denominator": _money(computed.denominator),
                "sample_size": computed.sample_size,
                "unit": str(computed.unit),
                "provenance": str(computed.provenance),
                # What the number would read as on a slide. Pinned because the
                # unit is part of the claim: "+88.2" and "88.2%" are the same
                # value and different statements, and NPS being shown as a
                # percentage is exactly the misreading the reconciliation has
                # to prevent.
                "formatted": computed.formatted(),
            }
        windows[name] = dict(sorted(values.items()))
    return windows


def _history(session: Session) -> dict[str, Any]:
    """What history the CRM actually holds — week 4 task 1, asserted.

    The plan records history as starting in September 2025 (Q-10). It starts on
    2025-07-29, and 68 of 123 sessions fall in 2025. Pinning the range turns
    "the source quietly grew a year of history" into a failing test rather than
    a figure nobody can explain in a stakeholder meeting.
    """
    first, last, sessions = session.execute(
        select(
            func.min(DimSession.session_date),
            func.max(DimSession.session_date),
            func.count(),
        ).select_from(DimSession)
    ).one()

    by_year = _pairs(
        session,
        select(func.extract("year", DimSession.session_date), func.count())
        .select_from(DimSession)
        .group_by(func.extract("year", DimSession.session_date)),
    )
    return {
        "first_session": first.isoformat() if first else None,
        "last_session": last.isoformat() if last else None,
        "sessions": sessions,
        "sessions_by_year": {str(int(float(year))): count for year, count in by_year.items()},
        # Nothing in `core` came from the workbook. If a spreadsheet-derived
        # figure is ever loaded it must carry a source tag, and this is the
        # assertion that no untagged one crept in.
        "sessions_without_a_time": session.scalar(
            select(func.count())
            .select_from(DimSession)
            .where(DimSession.duration_derivable.is_(False))
        ),
    }


def _totals(session: Session) -> dict[str, Any]:
    """The headline counts, and the two hour figures the workbook conflated."""

    def count(model: type) -> int | None:
        return session.scalar(select(func.count()).select_from(model))

    return {
        "programs": count(DimProgram),
        "sessions": count(DimSession),
        "trainers": count(DimTrainer),
        "employee_versions": count(DimEmployee),
        "employees": session.scalar(select(func.count(func.distinct(DimEmployee.odoo_id)))),
        "enrollments": count(FactEnrollment),
        "attendance": count(FactAttendance),
        "evaluations": count(FactEvaluation),
        "distinct_attendees": session.scalar(
            select(func.count(func.distinct(FactAttendance.employee_odoo_id)))
        ),
        # Two different questions with one name in the workbook. Catalogue
        # hours against learner-hours: 130.5 against 1,386 there, and pinned
        # separately here so they can never again be quoted interchangeably.
        "training_hours_delivered": _money(
            session.scalar(select(func.sum(DimSession.duration_hours)))
        ),
        "learner_hours": _money(session.scalar(select(func.sum(FactAttendance.learning_hours)))),
        "programs_completed": session.scalar(
            select(func.count()).where(DimProgram.computed_status == "completed")
        ),
        # The trap: `status` disagrees with `computed_status` on nearly every
        # program, and counting the wrong one turns 55 into 2.
        "programs_status_says_completed": session.scalar(
            select(func.count()).where(DimProgram.status == "completed")
        ),
    }


def _per_program(session: Session) -> dict[str, dict[str, int]]:
    """Per program, so rows moving between programs cannot cancel out.

    A join that attaches attendance to the wrong program leaves every total
    untouched. This is what notices.
    """
    programs: dict[str, dict[str, int]] = {}
    for model, key in (
        (FactEnrollment, "enrollments"),
        (FactAttendance, "attendance"),
        (FactEvaluation, "evaluations"),
    ):
        rows = session.execute(
            select(model.crm_program_id, func.count()).group_by(model.crm_program_id)
        ).all()
        for program_id, total in rows:
            programs.setdefault(str(program_id), {})[key] = total

    sessions = session.execute(
        select(DimSession.crm_program_id, func.count()).group_by(DimSession.crm_program_id)
    ).all()
    for program_id, total in sessions:
        programs.setdefault(str(program_id), {})["sessions"] = total

    return dict(sorted(programs.items(), key=lambda item: int(item[0])))


def _per_month(session: Session) -> dict[str, dict[str, int]]:
    """Monthly, because every trend on the dashboard is grouped this way.

    A date handled wrongly — a timezone applied twice, a scan timestamp used
    instead of a session date — moves rows across a month boundary without
    changing any total.
    """
    months: dict[str, dict[str, int]] = {}
    for model, column, key in (
        (DimSession, DimSession.session_date, "sessions"),
        (FactAttendance, FactAttendance.attended_date, "attendance"),
    ):
        # `date_trunc` and format in Python rather than `to_char`: the latter
        # needs the format string cast to text explicitly, and a bare literal
        # arrives as VARCHAR, which no overload of to_char accepts.
        bucket = func.date_trunc("month", column)
        rows = session.execute(
            select(bucket, func.count()).select_from(model).group_by(bucket)
        ).all()
        for month, total in rows:
            months.setdefault(month.strftime("%Y-%m"), {})[key] = total
    return dict(sorted(months.items()))


def _breakdowns(session: Session) -> dict[str, dict[str, int]]:
    """The dimensions coverage reporting groups by.

    Conforming failures show up here and nowhere else: P-05 split `Projects`
    from `Projects ` and would appear as a sector count that moved while every
    total held still.
    """
    current = DimEmployee.is_current
    return {
        "employees_by_company": _pairs(
            session,
            select(DimEmployee.company_name, func.count())
            .where(current)
            .group_by(DimEmployee.company_name),
        ),
        "employees_by_sector": _pairs(
            session,
            select(DimEmployee.sector, func.count()).where(current).group_by(DimEmployee.sector),
        ),
        "employees_by_job_level": _pairs(
            session,
            select(DimEmployee.job_level_name, func.count())
            .where(current)
            .group_by(DimEmployee.job_level_name),
        ),
        "employees_by_roster_status": _pairs(
            session,
            select(DimEmployee.on_current_roster, func.count())
            .where(current)
            .group_by(DimEmployee.on_current_roster),
        ),
        "programs_by_type": _pairs(
            session, select(DimProgram.type, func.count()).group_by(DimProgram.type)
        ),
        "programs_by_target": _pairs(
            session, select(DimProgram.target, func.count()).group_by(DimProgram.target)
        ),
        "programs_by_computed_status": _pairs(
            session,
            select(DimProgram.computed_status, func.count()).group_by(DimProgram.computed_status),
        ),
    }


def _identity(session: Session) -> dict[str, dict[str, int]]:
    """How every fact row found its person (FR-B04).

    A resolution order regression is invisible in the totals — the same rows
    exist either way — and shows here as attendance moving from `odoo_id` to
    `unresolved`, which is the workbook's 38 dropped attendees returning.
    """
    return {
        name: _pairs(
            session, select(model.identity_status, func.count()).group_by(model.identity_status)
        )
        for name, model in (
            ("enrollment", FactEnrollment),
            ("attendance", FactAttendance),
            ("evaluation", FactEvaluation),
        )
    }


def _exceptions(session: Session) -> dict[str, int]:
    """The open queue by rule.

    Pinned because a rule that stops firing looks exactly like the data getting
    better. Eight open exceptions on this dataset is a fact about the source,
    and it must not change quietly.
    """
    return _pairs(
        session,
        select(DqException.rule, func.count())
        .where(DqException.status == DqStatus.OPEN)
        .group_by(DqException.rule),
    )


def _defects(session: Session) -> dict[str, int]:
    """The workbook's defects, measured on this dataset.

    Not diagnostics — regression tests with numbers attached. Each one is a
    figure the workbook got wrong, and if the platform ever starts agreeing
    with the workbook again these move.
    """
    distinct_titles = session.scalar(select(func.count(func.distinct(DimProgram.title))))
    programs = session.scalar(select(func.count()).select_from(DimProgram)) or 0
    trainer_variants = session.scalar(
        select(func.count(func.distinct(DimSession.trainer_name_raw))).where(
            DimSession.trainer_name_raw.is_not(None)
        )
    )
    return {
        # P-02: grouping by title merges programs. This many would vanish.
        "programs_lost_to_title_grouping": programs - (distinct_titles or 0),
        # P-04: raw spellings against the people they resolve to.
        "trainer_name_variants": trainer_variants or 0,
        "trainers_after_merge": session.scalar(select(func.count()).select_from(DimTrainer)) or 0,
        # P-05: conformed sectors. Ungrouped they would be five more.
        "distinct_sectors": session.scalar(
            select(func.count(func.distinct(DimEmployee.sector))).where(DimEmployee.is_current)
        )
        or 0,
        # P-07: attendees the workbook dropped. Zero is the claim being pinned.
        "unresolved_attendance": session.scalar(
            select(func.count())
            .select_from(FactAttendance)
            .where(FactAttendance.employee_key.is_(None))
        )
        or 0,
        # P-13: the denominator spans this many companies.
        "companies_among_employees": session.scalar(
            select(func.count(func.distinct(DimEmployee.company_name))).where(
                DimEmployee.is_current
            )
        )
        or 0,
    }


def load(path: Path = GOLDEN) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def write(values: dict[str, Any], path: Path = GOLDEN) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(values, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    """Recompute the golden file from the frozen dataset.

    Deliberately not importable from the test suite. Regenerating must be a
    thing somebody *does*, in a commit, with a reviewer — never something a
    test run can do to itself while nobody is watching.
    """
    parser = argparse.ArgumentParser(description="Recompute tests/reference/golden.json")
    parser.add_argument(
        "--database-url",
        help="a scratch database to load the frozen dataset into (defaults to DATABASE_URL)",
    )
    parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from lnd.db import session_scope
    from lnd.reference.replay import replay

    with session_scope() as session:
        replay(session)
        values = compute(session)

    write(values)
    log.info("wrote %s", GOLDEN)
    log.info(
        "%d programs, %d attendance rows, %s learner hours",
        values["totals"]["programs"],
        values["totals"]["attendance"],
        values["totals"]["learner_hours"],
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - a script
    raise SystemExit(main())
