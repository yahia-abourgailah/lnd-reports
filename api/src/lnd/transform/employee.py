"""`core.dim_employee` — the workforce, versioned.

Reads the current version of every landed CRM employee, conforms it, and
records a new version wherever an attribute changed. Nothing else in the
platform knows what an employee looked like last month, because the CRM does
not: it answers "who works here now" and has no history at all. Every version
this table holds is one it observed and would otherwise have lost.

The population it describes is the participation denominator, which is the whole
reason it exists. The workbook divided five companies' attendance by a hardcoded
192 — and then by a roster covering one company — producing 66.7%. Against the
real roster the same numerator is 18.9%. That is not a refinement; it is the
difference between a figure that can be published and one that cannot.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from lnd.ingest import landing
from lnd.ingest.models import Entity, Source
from lnd.models.app_ import Quarantine, QuarantineReason
from lnd.models.core import DimEmployee
from lnd.models.ops import SourcePresence
from lnd.transform.accounting import RunTally
from lnd.transform.conform import Problem, conform_grade, conform_optional_text, conform_sector

#: The attributes a change to which opens a new version.
#:
#: `email` and `mobile` are in the list and that is a decision, not an oversight:
#: a corrected phone number costs one row at 1,427 employees, and excluding it
#: would mean the table quietly stops being a record of what the source said.
#: `status` is in the list because active→inactive is the single most
#: consequential change here — it is what moves somebody out of the denominator.
#: What a headcount calls an employee whose company the source did not send.
#: Explicit rather than dropped: an employee with no company is still an
#: employee, and hiding them would understate the denominator.
UNKNOWN_COMPANY = "(unknown)"

VERSIONED_ATTRIBUTES = (
    "odoo_id",
    "full_name",
    "email",
    "mobile",
    "department_name",
    "department_odoo_id",
    "company_name",
    "company_odoo_id",
    "sector",
    "position_name",
    "job_level_name",
    "job_level_grade",
    "status",
)


@dataclass(frozen=True)
class ConformedEmployee:
    employee_code: str
    attributes: dict[str, Any]


def _nested_id(payload: dict[str, Any], key: str) -> int | None:
    """`department` and `company` arrive as objects, or as nothing at all."""
    nested = payload.get(key)
    if not isinstance(nested, dict):
        return None
    value = nested.get("id") or nested.get("odoo_id")
    return int(value) if isinstance(value, int | str) and str(value).isdigit() else None


def _nested_name(payload: dict[str, Any], key: str) -> str | None:
    nested = payload.get(key)
    if isinstance(nested, dict):
        return conform_optional_text(nested.get("name"))
    return conform_optional_text(nested)


def conform(payload: dict[str, Any]) -> tuple[ConformedEmployee | None, Problem | None]:
    """One raw user object into dimension attributes, or the reason it cannot.

    Two fields are required and neither can be defaulted. Without
    `employee_code` the row cannot be joined to anything — it is the business
    key every fact resolves through — and without `odoo_id` an attendance
    record cannot be traced back to it. A row missing either is a person the
    platform can see but never count, which is precisely what the queue is for.
    """
    employee_code = conform_optional_text(payload.get("employee_code"))
    if employee_code is None:
        return None, Problem(
            QuarantineReason.MISSING_REQUIRED,
            "employee_code is absent, so the row cannot be joined to any fact",
        )

    odoo_id = payload.get("odoo_id")
    if not isinstance(odoo_id, int | str) or not str(odoo_id).isdigit():
        return None, Problem(
            QuarantineReason.MISSING_REQUIRED,
            f"odoo_id {odoo_id!r} is absent or not an id, so attendance cannot resolve to it",
        )

    full_name = conform_optional_text(payload.get("full_name")) or conform_optional_text(
        payload.get("name")
    )
    if full_name is None:
        return None, Problem(
            QuarantineReason.MISSING_REQUIRED,
            f"{employee_code} has no name, which no report can display",
        )

    sector, sector_problem = conform_sector(payload.get("sector"))
    if sector_problem is not None:
        return None, sector_problem

    grade, grade_problem = conform_grade(payload.get("job_level_grade"))
    if grade_problem is not None:
        return None, grade_problem

    return (
        ConformedEmployee(
            employee_code=employee_code,
            attributes={
                "odoo_id": int(odoo_id),
                "full_name": full_name,
                "email": conform_optional_text(payload.get("email")),
                "mobile": conform_optional_text(payload.get("mobile")),
                "department_name": _nested_name(payload, "department"),
                "department_odoo_id": _nested_id(payload, "department"),
                "company_name": _nested_name(payload, "company"),
                "company_odoo_id": _nested_id(payload, "company"),
                "sector": sector,
                "position_name": _nested_name(payload, "position"),
                "job_level_name": conform_optional_text(payload.get("job_level_name")),
                "job_level_grade": grade,
                "status": conform_optional_text(payload.get("status")),
            },
        ),
        None,
    )


def _program_people(session: Session) -> dict[str, dict[str, Any]]:
    """Every `user` object embedded in a program payload, one per person.

    The second source. It is where `position`, `email` and `mobile` live — the
    roster carries none of the three — and it is the only record of the 149
    trained people `get_users` no longer returns.

    A person appears once per program they touched, 1,060 objects for 419
    people. The last one seen wins, and that is safe because they are the same
    person: the fields differ only where the source corrected something, and
    there is no ordering by which an earlier copy would be the better answer.
    """
    people: dict[str, dict[str, Any]] = {}
    for record in landing.current(session, source=Source.CRM, entity=Entity.PROGRAM):
        for entry in record.payload.get("users") or []:
            if not isinstance(entry, dict):
                continue
            user = entry.get("user")
            if not isinstance(user, dict):
                continue
            code = conform_optional_text(user.get("employee_code"))
            if code is not None:
                people[code] = user
    return people


#: Fields the roster does not carry at all, so a program payload is not
#: overriding the roster when it fills them — it is the only source there is.
PROGRAM_ONLY_FIELDS = ("position_name", "email", "mobile")


def _merge(roster: dict[str, Any], from_program: dict[str, Any]) -> dict[str, Any]:
    """Roster attributes, with the three fields only programs carry filled in.

    Deliberately not a general merge. The roster is authoritative for everything
    it returns — it is today's answer, where a program payload is a snapshot
    from whenever that program ran — so only the fields the roster has no
    opinion about are taken from elsewhere.
    """
    merged = dict(roster)
    for field in PROGRAM_ONLY_FIELDS:
        if merged.get(field) is None and from_program.get(field) is not None:
            merged[field] = from_program[field]
    return merged


def _current_versions(session: Session) -> dict[str, DimEmployee]:
    rows = session.scalars(select(DimEmployee).where(DimEmployee.is_current)).all()
    return {row.employee_code: row for row in rows}


def _changed(existing: DimEmployee, attributes: dict[str, Any]) -> bool:
    return any(getattr(existing, name) != attributes[name] for name in VERSIONED_ATTRIBUTES)


def build(
    session: Session,
    *,
    as_of: dt.datetime,
    sync_run_id: int | None = None,
    close_absent: bool = True,
) -> RunTally:
    """Bring `dim_employee` up to date with the current landed roster.

    Idempotent: running it twice with an unchanged roster opens no versions,
    which is what `unchanged` in the tally counts. That property is what makes
    it safe to call after every sync rather than only after a change, and it is
    tested rather than assumed.

    `close_absent` closes the version of anyone `ops.source_presence` has marked
    gone — a leaver. It defaults on but must be off for a partial load, because
    absence from a partial pull means nothing was asked, not that nobody
    answered. Only a full pass can conclude a person has left, which is the same
    rule the reconcile applies to `raw`.
    """
    tally = RunTally(source=Source.CRM, entity=Entity.EMPLOYEE)

    roster = landing.current(session, source=Source.CRM, entity=Entity.EMPLOYEE)
    from_programs = _program_people(session)

    # Everything the roster returns, plus everyone the programs know about that
    # it does not. A person in both is one received row, not two — the program
    # copy fills in fields rather than arriving as a separate identity.
    roster_payloads = [(record.source_id, record.payload, True) for record in roster]
    roster_codes = {
        code
        for code in (
            conform_optional_text(record.payload.get("employee_code")) for record in roster
        )
        if code is not None
    }
    historical = [
        (str(user.get("odoo_id") or code), user, False)
        for code, user in sorted(from_programs.items())
        if code not in roster_codes
    ]

    tally.received = len(roster_payloads) + len(historical)

    existing = _current_versions(session)
    seen: set[str] = set()

    for source_id, payload, on_roster in roster_payloads + historical:
        conformed, problem = conform(payload)
        if conformed is None:
            assert problem is not None
            session.add(
                Quarantine(
                    source=Source.CRM,
                    entity=Entity.EMPLOYEE,
                    reason=problem.reason,
                    source_id=source_id,
                    detail=problem.detail,
                    payload=payload,
                    sync_run_id=sync_run_id,
                )
            )
            tally.quarantine(problem.reason.value)
            continue

        attributes = conformed.attributes
        if on_roster and conformed.employee_code in from_programs:
            program_view, program_problem = conform(from_programs[conformed.employee_code])
            if program_view is not None and program_problem is None:
                attributes = _merge(attributes, program_view.attributes)

        # A second landed row for one employee_code is a genuine ambiguity: two
        # payloads claim the same person and there is no rule for choosing. It
        # is quarantined rather than resolved arbitrarily, because arbitrary
        # here means the roster silently changes shape between runs.
        if conformed.employee_code in seen:
            session.add(
                Quarantine(
                    source=Source.CRM,
                    entity=Entity.EMPLOYEE,
                    reason=QuarantineReason.DUPLICATE,
                    source_id=source_id,
                    detail=(
                        f"employee_code {conformed.employee_code} arrived on more than one "
                        "user object in the same roster"
                    ),
                    payload=payload,
                    sync_run_id=sync_run_id,
                )
            )
            tally.quarantine(QuarantineReason.DUPLICATE.value)
            continue
        seen.add(conformed.employee_code)

        previous = existing.get(conformed.employee_code)
        if previous is None:
            session.add(
                DimEmployee(
                    employee_code=conformed.employee_code,
                    valid_from=as_of,
                    is_current=True,
                    is_estimated=False,
                    on_current_roster=on_roster,
                    **attributes,
                )
            )
            tally.count()
            continue

        if not _changed(previous, attributes) and previous.on_current_roster == on_roster:
            tally.keep_unchanged()
            continue

        previous.valid_to = as_of
        previous.is_current = False
        session.add(
            DimEmployee(
                employee_code=conformed.employee_code,
                valid_from=as_of,
                is_current=True,
                is_estimated=False,
                on_current_roster=on_roster,
                **attributes,
            )
        )
        tally.count()

    if close_absent:
        _close_leavers(session, as_of=as_of)

    tally.assert_balanced()
    return tally


def _close_leavers(session: Session, *, as_of: dt.datetime) -> int:
    """Close the version of anyone the source has stopped returning.

    `ops.source_presence` is the authority, not this run's roster. Absence from
    the roster is ambiguous — the person may have left, or their row may have
    been quarantined this pass, or the pull may have been partial — and only
    presence distinguishes those, because only a full reconcile is allowed to
    write `is_present = false` there in the first place. Reading it here means
    the rule that decides somebody has left is stated once, for `raw` and
    `core` alike, rather than twice with a chance of disagreeing.

    Deliberately not counted in the tally. The invariant accounts for rows that
    arrived; a leaver is the absence of a row, so folding it in would make
    `received` and `counted` disagree by exactly the number of people who left —
    an alarm that fires on the pipeline working correctly.
    """
    gone = set(
        session.scalars(
            select(SourcePresence.source_id).where(
                SourcePresence.source == Source.CRM,
                SourcePresence.entity == Entity.EMPLOYEE,
                SourcePresence.is_present.is_(False),
            )
        ).all()
    )
    if not gone:
        return 0

    closed = 0
    for row in session.scalars(select(DimEmployee).where(DimEmployee.is_current)).all():
        if str(row.odoo_id) not in gone:
            continue
        row.valid_to = as_of
        row.is_current = False
        closed += 1
    return closed


# ---------------------------------------------------------------------------
# headcount
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Headcount:
    """A denominator, with the honesty attached to it.

    `is_estimated` is not decoration. Asked for a date before the platform
    started snapshotting, the answer is the earliest roster carried backwards —
    which is the only answer available and is not the same kind of fact as a
    date after go-live. A caller that renders the two identically publishes a
    guess as a measurement, so the flag travels with the number rather than
    being something the caller has to remember to look up.
    """

    as_of: dt.datetime
    total: int
    by_company: dict[str, int]
    is_estimated: bool


def _live_at(as_of: dt.datetime) -> Any:
    return and_(
        DimEmployee.valid_from <= as_of,
        or_(DimEmployee.valid_to.is_(None), DimEmployee.valid_to > as_of),
    )


def _company_filtered(statement: Select[Any], entity_set: frozenset[str] | None) -> Select[Any]:
    if entity_set is None:
        return statement
    return statement.where(DimEmployee.company_name.in_(sorted(entity_set)))


def headcount_as_of(
    session: Session,
    *,
    as_of: dt.datetime,
    entity_set: frozenset[str] | None = None,
    active_only: bool = True,
) -> Headcount:
    """How many people were employed at a moment, per company.

    `entity_set` is P-13: the participation denominator has to span every legal
    entity the CRM enrolls from. Attendance spans five companies, so a headcount
    restricted to one understates participation by a factor of roughly three —
    which is how the workbook arrived at 66.7% for a figure that is 18.9%.
    Passing `None` counts every entity, and that is the default because the
    narrower answer is the one that needs justifying.
    """
    earliest = session.scalar(select(func.min(DimEmployee.valid_from)))

    # One expression object, used in both the select list and the GROUP BY.
    # Building it twice renders two distinct bind parameters, and PostgreSQL
    # cannot see that $1 and $3 are the same value — it rejects the grouping.
    company = func.coalesce(DimEmployee.company_name, UNKNOWN_COMPANY)

    # `on_current_roster` first, and not optional. The dimension deliberately
    # holds 149 people the roster no longer returns so their attendance can
    # still key; counting them here would inflate the denominator with leavers
    # and with two companies that no longer exist.
    statement = select(company, func.count()).where(_live_at(as_of), DimEmployee.on_current_roster)
    if active_only:
        statement = statement.where(DimEmployee.status == "active")
    statement = _company_filtered(statement, entity_set).group_by(company)

    by_company: dict[str, int] = {row[0]: row[1] for row in session.execute(statement).all()}

    return Headcount(
        as_of=as_of,
        total=sum(by_company.values()),
        by_company=by_company,
        is_estimated=earliest is None or as_of < earliest,
    )
