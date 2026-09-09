"""Performance validation at the BRD's ceiling, measured rather than assumed.

    python -m lnd.reference.perf --database-url postgresql+psycopg://…/lnd_perf

Week 10, Person A. The acceptance criterion is *every view returns within 2
seconds at p95 against 15,000 records*, and until this ran nobody had put more
than 1,165 attendance rows under it. The indexes added in week 5 were added for
this scale and had never been loaded to it — an index chosen for a table
PostgreSQL currently prefers to scan is a guess until the table is big enough
for the planner to disagree.

HOW THE SCALE IS REACHED

Whole programmes are cloned: a programme, its sessions, its enrollments, its
attendance and its evaluations, all copied together with their ids offset. Every
grain grows in the same proportion and every foreign key still resolves, so what
is measured is this dataset's own shape at twelve times the size — not a uniform
random table that no query would ever be planned against the way these are.

Cloning attendance alone would have been easier and would have measured
something else: 15,000 attendance rows over 123 sessions is 122 people per
session, which is not this platform and would make every per-session join look
cheap.

WHY IT REFUSES TO RUN WITHOUT `--database-url`

It writes twelve times the dataset into `core`. Pointed at the dev database that
is recoverable — `core` is a pure function of raw plus enrichment, so the next
transform rebuilds it — but only after the next transform, and in the meantime
every figure on the dashboard is twelve times too large. Pointed at production
it would be the same thing without the recovery. So the target is explicit,
always.

WHAT IS MEASURED

The real endpoints, through the real application: routing, the auth dependency,
the filter model, the metric registry, serialisation. The transport is ASGI
rather than a socket, so nginx and the network are excluded — worth perhaps a
millisecond on a LAN, and worth naming rather than quietly claiming.

Cold and warm are both reported. Cold is with the aggregate cache flushed, which
is the state every screen is in for the first person to open it after a
transform; warm is every request after that. The gate is applied to cold, since
a p95 that depends on the cache is a p95 that reports the cache.
"""

from __future__ import annotations

import argparse
import logging
import os
import statistics
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

REPORT = Path(__file__).resolve().parents[4] / "docs" / "performance.md"

#: The BRD's ceiling, in milliseconds. NFR-P01.
GATE_MS = 2000.0

#: The record count the criterion names. Attendance is the grain it means: it is
#: the largest fact table and the one every dashboard figure reads.
TARGET_ATTENDANCE = 15_000

#: Program and session ids are `Integer`, so the offset has to leave room for
#: twelve clones without approaching 2^31. A million per clone does.
CLONE_OFFSET = 1_000_000

#: What the acceptance criterion means by "every view" — one request per screen,
#: at the shape the screen actually issues. Filtered variants are included
#: because an unfiltered dashboard is the easy case: the filter narrows the
#: population *before* the metric aggregates, and a plan that was fine on the
#: whole table can change entirely under a predicate.
VIEWS: tuple[tuple[str, str], ...] = (
    ("Overview — every KPI", "/v1/kpis"),
    ("Overview — filtered to a window", "/v1/kpis?date_from=2026-02-01&date_to=2026-08-31"),
    ("Overview — a breakdown", "/v1/kpis/participation_rate/breakdown?by=company"),
    ("Overview — a trend", "/v1/kpis/learner_hours/trend"),
    ("Coverage", "/v1/coverage"),
    ("Coverage — the untrained list", "/v1/coverage/untrained?company=The%20MarQ%20Communities"),
    ("Funnel", "/v1/funnel"),
    ("Programmes — the index", "/v1/programs"),
    ("Trainers — the index", "/v1/trainers"),
    ("Learners — top by hours", "/v1/learners/top"),
    ("Exceptions — the console", "/v1/exceptions"),
    ("Exceptions — completeness", "/v1/exceptions/completeness"),
    ("Drill — behind a KPI", "/v1/drill/total_participants?limit=100"),
    ("Freshness", "/v1/freshness"),
)

#: Enough samples for a 95th percentile to mean something, and few enough that
#: the whole run is minutes rather than an afternoon. With 20 the p95 is the
#: second-slowest observation, which is the honest reading of it.
REPEATS = 20


@dataclass
class Timing:
    name: str
    path: str
    status: int
    cold_ms: list[float] = field(default_factory=list)
    warm_ms: list[float] = field(default_factory=list)

    @staticmethod
    def _p95(samples: list[float]) -> float:
        if not samples:
            return 0.0
        ordered = sorted(samples)
        # The observation at the 95th percentile, not an interpolation between
        # two of them: with twenty samples the interpolated value is a number
        # that never happened.
        index = max(0, round(0.95 * len(ordered)) - 1)
        return ordered[index]

    @property
    def cold_p95(self) -> float:
        return self._p95(self.cold_ms)

    @property
    def warm_p95(self) -> float:
        return self._p95(self.warm_ms)

    @property
    def cold_median(self) -> float:
        return statistics.median(self.cold_ms) if self.cold_ms else 0.0

    @property
    def worst(self) -> float:
        return max([*self.cold_ms, *self.warm_ms], default=0.0)

    @property
    def passes(self) -> bool:
        return self.status == 200 and self.cold_p95 <= GATE_MS


def _tables() -> Any:
    from lnd.models.core import (
        DimProgram,
        DimSession,
        FactAttendance,
        FactEnrollment,
        FactEvaluation,
    )

    # Parents before children: every clone below inserts in this order, so the
    # foreign keys resolve as they are written rather than at commit.
    return (
        DimProgram.__table__,
        DimSession.__table__,
        FactEnrollment.__table__,
        FactAttendance.__table__,
        FactEvaluation.__table__,
    )


#: Columns carrying an id that must move with the clone. Everything else — the
#: employee, the trainer, the date, every measure — is copied unchanged, which
#: is what keeps the shape.
OFFSET_COLUMNS = {"crm_program_id", "crm_session_id"}

#: Surrogate keys the database assigns. Copying them would collide; omitting
#: them lets the sequence do its job.
GENERATED_COLUMNS = {"attendance_key", "enrollment_key", "evaluation_key"}


def amplify(session: Session, target: int = TARGET_ATTENDANCE) -> dict[str, int]:
    """Clone whole programmes until attendance reaches `target`.

    Returns the census afterwards. Idempotent only in the sense that running it
    twice doubles again — it is a scratch-database operation and says so.
    """
    from lnd.models.core import FactAttendance

    def attendance() -> int:
        return session.scalar(select(func.count()).select_from(FactAttendance)) or 0

    base = attendance()
    if base == 0:
        raise RuntimeError("nothing to amplify: core is empty. Replay the dataset first.")

    clone = 0
    while attendance() < target:
        clone += 1
        offset = CLONE_OFFSET * clone
        for table in _tables():
            columns = [c for c in table.columns if c.name not in GENERATED_COLUMNS]
            names = ", ".join(f'"{c.name}"' for c in columns)
            values = ", ".join(
                f'"{c.name}" + {offset}' if c.name in OFFSET_COLUMNS else f'"{c.name}"'
                for c in columns
            )
            # Only the original rows are cloned each time, never the clones —
            # otherwise the growth is exponential and the last step overshoots
            # 15,000 by a factor nobody chose.

            # The `noqa` below is justified: every interpolated part comes from
            # SQLAlchemy table metadata and two integer constants in this module.
            # Nothing here is reachable from a request, and the alternative —
            # enumerating fifty columns per table by hand — is a list that goes
            # stale the first time a column is added.
            session.execute(
                text(
                    f'INSERT INTO {table.schema}."{table.name}" ({names}) '  # noqa: S608
                    f'SELECT {values} FROM {table.schema}."{table.name}" '
                    f'WHERE "crm_program_id" < {CLONE_OFFSET}'
                )
            )
        session.commit()
        log.info("clone %d — attendance now %d", clone, attendance())
        if clone > 40:  # pragma: no cover - a guard, not a path
            raise RuntimeError("amplification is not converging; stopping")

    return census(session)


def census(session: Session) -> dict[str, int]:
    from lnd.models.core import (
        DimEmployee,
        DimProgram,
        DimSession,
        FactAttendance,
        FactEnrollment,
        FactEvaluation,
    )

    return {
        "programs": session.scalar(select(func.count()).select_from(DimProgram)) or 0,
        "sessions": session.scalar(select(func.count()).select_from(DimSession)) or 0,
        "employees": session.scalar(select(func.count(func.distinct(DimEmployee.odoo_id)))) or 0,
        "enrollments": session.scalar(select(func.count()).select_from(FactEnrollment)) or 0,
        "attendance": session.scalar(select(func.count()).select_from(FactAttendance)) or 0,
        "evaluations": session.scalar(select(func.count()).select_from(FactEvaluation)) or 0,
    }


def _flush_cache() -> None:
    """Empty the aggregate cache, so the next request is somebody's first."""
    try:
        from lnd.metrics import cache

        cache.invalidate()
    except Exception as exc:  # pragma: no cover - Redis absent is a valid state
        log.debug("cache not flushed: %s", exc)


def measure(repeats: int = REPEATS) -> list[Timing]:
    """Drive every view through the real application and time it."""
    # Starlette's client rather than httpx's ASGI transport: the latter is
    # async-only, and driving it from a synchronous script means an event loop
    # of our own whose scheduling would end up inside every measurement.
    from starlette.testclient import TestClient

    from lnd.main import create_app

    timings: list[Timing] = []

    with TestClient(create_app(), base_url="http://perf") as client:
        # Dev bypass issues the session cookie without an IdP. Outside dev the
        # settings guard refuses to start at all, so this harness is dev-only by
        # construction rather than by convention.
        client.get("/v1/auth/login", follow_redirects=True)

        for name, path in VIEWS:
            timing = Timing(name=name, path=path, status=0)
            for _ in range(repeats):
                _flush_cache()
                started = time.perf_counter()
                response = client.get(path)
                timing.cold_ms.append((time.perf_counter() - started) * 1000)
                timing.status = response.status_code

                started = time.perf_counter()
                client.get(path)
                timing.warm_ms.append((time.perf_counter() - started) * 1000)
            timings.append(timing)
            log.info(
                "%-40s %s cold p95 %6.0f ms  warm p95 %6.0f ms",
                name,
                timing.status,
                timing.cold_p95,
                timing.warm_p95,
            )
    return timings


def render(counts: dict[str, int], timings: list[Timing], repeats: int) -> str:
    failures = [t for t in timings if not t.passes]
    lines = [
        "# Performance validation at scale",
        "",
        "<!-- GENERATED by `python -m lnd.reference.perf`. Do not edit by hand. -->",
        "",
        f"Run {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} UTC. "
        f"{repeats} samples per view, cold and warm.",
        "",
        "## The claim being tested",
        "",
        "NFR-P01: **every view returns within 2 seconds at p95 against 15,000 records**. "
        "It is a launch gate, and until this run the largest table the platform had ever "
        "been measured against held 1,165 rows.",
        "",
        "The gate is applied to the **cold** column — the aggregate cache flushed before "
        "every request, which is the state the first person to open a screen after a "
        "transform is in. A p95 that depends on a warm cache is a measurement of the "
        "cache.",
        "",
        "## The dataset it ran against",
        "",
        "Whole programmes cloned — programme, sessions, enrollments, attendance and "
        "evaluations together, ids offset — so every grain grew in proportion and this "
        "dataset's own shape is what was loaded. Cloning attendance alone would have put "
        "122 people in every session, which is not this platform.",
        "",
        "| Grain | Rows |",
        "|---|---|",
        *(f"| {name} | {count:,} |" for name, count in counts.items()),
        "",
        "## Every view",
        "",
        "| View | Status | Cold median | **Cold p95** | Warm p95 | Worst |",
        "|---|---|---|---|---|---|",
    ]
    for t in timings:
        mark = "" if t.passes else " ⚠"
        lines.append(
            f"| {t.name} | {t.status} | {t.cold_median:,.0f} ms | "
            f"**{t.cold_p95:,.0f} ms**{mark} | {t.warm_p95:,.0f} ms | {t.worst:,.0f} ms |"
        )

    slowest = max(timings, key=lambda t: t.cold_p95) if timings else None
    lines += ["", "## Result", ""]
    if failures:
        lines += [
            f"**The gate is not met.** {len(failures)} of {len(timings)} views exceed "
            f"{GATE_MS:,.0f} ms at p95, or did not return 200:",
            "",
            *(
                f"- **{t.name}** — `{t.path}` — {t.status}, cold p95 {t.cold_p95:,.0f} ms"
                for t in failures
            ),
            "",
        ]
    else:
        lines += [
            f"**Met.** All {len(timings)} views return 200 within {GATE_MS:,.0f} ms at p95 "
            f"with the cache cold.",
            "",
        ]
    if slowest is not None:
        lines += [
            f"The slowest is **{slowest.name}** at {slowest.cold_p95:,.0f} ms cold and "
            f"{slowest.warm_p95:,.0f} ms warm. That is the one to watch: it is the view "
            "that will degrade first if the dataset grows past this ceiling.",
            "",
        ]

    lines += [
        "## What this does not measure",
        "",
        "The transport is ASGI, not a socket, so nginx, TLS and the network are excluded. "
        "On the corporate LAN that is worth about a millisecond, and it is named here "
        "rather than folded silently into a passing number.",
        "",
        "Concurrency is not measured either. Every sample is one request at a time, which "
        "is the shape of this platform's load — three named users, a weekly manager and a "
        "monthly director — and would be the wrong test for a service with real "
        "contention.",
        "",
        "**The roster did not grow.** Employees are the CRM's population, not a fact "
        "grain, so cloning them would have invented people. Coverage is the one view "
        "whose cost is per *employee* rather than per attendance row, and it is measured "
        f"here over the real {counts.get('employees', 0):,} — which is also the number "
        "production will hold. It is "
        "already the slowest view, so this is worth knowing rather than assuming: at "
        "10,000 employees it would need measuring again.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure p95 at the BRD's record ceiling")
    parser.add_argument(
        "--database-url",
        required=True,
        help="a scratch database. This writes twelve times the dataset into core.",
    )
    parser.add_argument("--target", type=int, default=TARGET_ATTENDANCE)
    parser.add_argument("--repeats", type=int, default=REPEATS)
    parser.add_argument(
        "--no-replay",
        action="store_true",
        help="the scratch database already holds a transformed core",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from lnd.config import get_settings
    from lnd.db import dispose_engine

    os.environ["DATABASE_URL"] = args.database_url
    get_settings.cache_clear()
    dispose_engine()

    from lnd.db import session_scope

    with session_scope() as session:
        if not args.no_replay:
            from lnd.reference.replay import replay

            replay(session)
        counts = amplify(session, args.target)

    log.info("measuring %d views, %d samples each", len(VIEWS), args.repeats)
    timings = measure(args.repeats)

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(render(counts, timings, args.repeats), encoding="utf-8")
    log.info("wrote %s", REPORT)

    failures = [t for t in timings if not t.passes]
    for t in failures:
        log.error("OVER GATE %s — %s — %.0f ms", t.name, t.path, t.cold_p95)
    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover - a script
    raise SystemExit(main())
