"""`core.dim_employee`: the Slowly Changing Dimension Type 2 loader (FR-B02).

Participation Rate needs headcount *as of the period*, not today's. Without
versioning, February's rate silently changes every time somebody joins or
leaves — the same instability as the hardcoded 192 the platform exists to
remove (P-01), except that nobody would ever notice this one. A published
February figure that quietly differs from the February figure published last
month is worse than a wrong constant, because the wrong constant at least holds
still.

HOW A VERSION IS DECIDED

By hash, not by comparison. Every attribute that matters is hashed into
`attribute_hash`, and a re-read whose hash matches the current row does
nothing at all. That is the same mechanism as `raw.source_record.payload_hash`,
one layer up, and using the same function means the two layers cannot disagree
about whether something changed.

Field-by-field comparison would need updating every time a column is added, and
the failure mode of forgetting is silent: the new column simply stops
triggering versions and nobody finds out until someone asks why a sector change
was never recorded.

WHAT `valid_from` ACTUALLY MEANS, AND WHY THE FIRST VERSION IS BACKDATED

Observation time. The source sends no change history and no effective dates, so
the honest claim is "this is what the CRM said about this person when we
looked", not "this became true on this date". Inventing an effective date would
make the as-of query look more precise than the data supports.

That is correct and, taken alone, useless. Our first snapshot is the day the
platform first synced; the CRM holds sessions back to September 2025. If every
version began at observation time, `as_of()` would return nothing for every
session that has ever happened, and the participation rate it exists to serve
could only ever be computed for the future.

So the first version of a person is backdated to `HISTORY_EPOCH` and flagged
`is_estimated`, while `observed_from` keeps the real moment. The claim being
made is explicit: *these are today's attributes, assumed to have held before we
were watching.* Usually true. Occasionally very wrong — somebody who changed
department in March is reported all the way back under the department they sit
in now. Which is why the flag is a column and not a comment: `headcount_as_of`
reads it and hands the caller a labelled figure rather than a bare number.

Later versions are not backdated. Their `valid_from` is an observation we
genuinely made, give or take the thirty minutes since the previous pass.

THE SOURCE IS NOT A ROSTER

Every person here comes from a `user` object nested inside a program, so this
dimension knows only people who touched training. Sufficient for every
attribute breakdown; insufficient for the two metrics that need a denominator
over the whole company. See the week-3 notes.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from lnd.models.core import DimEmployee
from lnd.sources.crm.models import User
from lnd.transform.conform import attribute_hash, normalise, trim

log = logging.getLogger(__name__)

#: How far back a first version's window is extended. A fixed constant rather
#: than "the earliest session in the data", which would be a *moving* floor:
#: loading one older program would silently change the start of every person's
#: history, and with it a published figure. Far enough back that no plausible
#: backfill predates it, and its only role is to make the as-of query resolve.
HISTORY_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class EmployeeLoad:
    """What one pass did to the dimension."""

    seen: int
    inserted: int
    versioned: int
    unchanged: int


@dataclass(frozen=True)
class Headcount:
    """A headcount, and everything a caller must know before publishing it.

    A bare integer would be a liability here. Two separate things can make this
    number not mean what a reader assumes, and both are invisible in the digit
    itself — so they travel with it, and the metric layer is expected to render
    them rather than drop them.
    """

    count: int
    moment: datetime
    #: The companies counted, or None for "everyone we know about".
    entity_set: tuple[str, ...] | None

    #: The date precedes our first observation of some or all of these people,
    #: so their attributes at that date are assumed rather than known. True for
    #: every date before the platform's first sync — which is every date in the
    #: CRM's history today.
    is_estimated: bool

    #: FALSE UNTIL Q-15 IS ANSWERED, AND THE REASON THIS CANNOT YET BE A
    #: PARTICIPATION-RATE DENOMINATOR.
    #:
    #: `dim_employee` is built from `user` objects nested inside programs, so it
    #: contains people who touched training and nobody else. Dividing
    #: participants by it would produce a participation rate of very nearly
    #: 100% — an answer that is not merely wrong but wrong in the flattering
    #: direction, which is the kind that gets published.
    #:
    #: P-01 was a hardcoded 192; this would be a defect of the same family with
    #: better provenance. So the flag is returned, false, on every call until
    #: the CRM can enumerate its enrollable population, and FR-A05c's rule
    #: applies: publish the ratio only when numerator and denominator cover the
    #: same population.
    covers_enrollable_population: bool = False

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return self.count > 0


def attributes_of(user: User, *, on_current_roster: bool = True) -> dict[str, object]:
    """The conformed attributes of one person, and the input to the hash.

    Conformance happens here rather than at read time (FR-B07): a trailing
    space that reaches the dimension is a trailing space that splits a sector
    in every breakdown built on it, and trimming in the view would mean
    trimming in every view. That is P-05, and this is where it stops.

    National ID and payscale are absent by decision, not oversight — no metric
    in section 9 needs either, and ingesting them would widen the platform's
    data-protection obligations for nothing (NFR-06).
    """
    return {
        "employee_code": trim(user.employee_code),
        "email": trim(user.email),
        "full_name": trim(user.full_name or user.name),
        "normalised_name": normalise(user.full_name or user.name),
        "sector": user.sector_conformed,
        "department_name": trim(user.department.name) if user.department else None,
        "company_name": trim(user.company.name) if user.company else None,
        "position_name": trim(user.position.name) if user.position else None,
        "job_level_name": trim(user.job_level_name),
        "job_level_grade": user.job_level_grade,
        "status": trim(user.status),
        # Versioned like every other attribute, so "left in March, back in
        # July" is answerable rather than overwritten — and so it takes part in
        # the hash, which is what makes a person leaving the roster a change
        # worth opening a version for.
        "on_current_roster": on_current_roster,
    }


def load_employees(
    session: Session,
    users: dict[str, User],
    *,
    roster_ids: set[str] | None = None,
) -> EmployeeLoad:
    """Bring `dim_employee` up to date with `users`, keyed by `odoo_id`.

    Three outcomes per person, and the middle one is the whole point:

        unknown          insert a current version
        changed          close the current version, open a new one
        unchanged        do nothing, and touch no timestamp

    "Do nothing" has to be genuinely nothing. A pass that rewrote
    `transformed_at` on every employee every thirty minutes would make the
    table's own history useless for answering when a person actually changed.

    `roster_ids` is who `get_users` currently returns. Anyone in `users` but not
    in it has trained and since gone — a leaver, or somebody from a company that
    no longer exists — and is loaded with `on_current_roster` false so their
    attendance still keys while the denominator stays honest. Passing None means
    "no roster was read", and everyone keeps whatever flag they already had
    rather than being marked departed by an argument that was never supplied.
    """
    if not users:
        return EmployeeLoad(seen=0, inserted=0, versioned=0, unchanged=0)

    now = datetime.now(UTC)

    current = {
        odoo_id: (employee_key, existing_hash)
        for odoo_id, employee_key, existing_hash in session.execute(
            select(DimEmployee.odoo_id, DimEmployee.employee_key, DimEmployee.attribute_hash)
            .where(DimEmployee.is_current)
            .where(DimEmployee.odoo_id.in_(list(users)))
        ).all()
    }

    to_close: list[int] = []
    to_insert: list[dict[str, object]] = []
    unchanged = 0

    known_roster = {
        odoo_id
        for (odoo_id,) in session.execute(
            select(DimEmployee.odoo_id)
            .where(DimEmployee.is_current)
            .where(DimEmployee.on_current_roster)
        ).all()
    }

    for odoo_id, user in users.items():
        on_roster = odoo_id in roster_ids if roster_ids is not None else odoo_id in known_roster
        attributes = attributes_of(user, on_current_roster=on_roster)
        digest = attribute_hash(attributes)

        existing = current.get(odoo_id)
        if existing is not None and existing[1] == digest:
            unchanged += 1
            continue

        if existing is not None:
            to_close.append(existing[0])

        # A person we have never seen gets their window extended back over the
        # history the CRM holds and we did not watch. A person we have seen
        # before does not: we were there for the change, near enough.
        first_version = existing is None
        to_insert.append(
            {
                "odoo_id": odoo_id,
                **attributes,
                "valid_from": HISTORY_EPOCH if first_version else now,
                "observed_from": now,
                "is_estimated": first_version,
                "valid_to": None,
                "is_current": True,
                "attribute_hash": digest,
            }
        )

    # Close first. The partial unique index allows exactly one current row per
    # person, so inserting the new version before closing the old one would
    # collide — and the collision is the index doing its job, not a bug to work
    # around. Both statements are in the caller's transaction, so a failure
    # between them leaves the person with their old version rather than none.
    if to_close:
        session.execute(
            update(DimEmployee)
            .where(DimEmployee.employee_key.in_(to_close))
            .values(is_current=False, valid_to=now)
        )

    if to_insert:
        session.execute(insert(DimEmployee), to_insert)

    result = EmployeeLoad(
        seen=len(users),
        inserted=len(to_insert) - len(to_close),
        versioned=len(to_close),
        unchanged=unchanged,
    )
    log.info(
        "loaded employees",
        extra={
            "event": "transform.employees.loaded",
            "seen": result.seen,
            "inserted": result.inserted,
            "versioned": result.versioned,
            "unchanged": result.unchanged,
        },
    )
    return result


def as_of(session: Session, *, odoo_id: str, moment: datetime) -> DimEmployee | None:
    """The version of a person that was current at `moment`.

    The query the participation-rate denominator is built from, and the reason
    the dimension is versioned at all. Half-open on purpose — `valid_from <=
    moment < valid_to` — so that a person whose record changed at noon is
    counted exactly once by a query at noon rather than twice or not at all.
    """
    return session.execute(
        select(DimEmployee)
        .where(DimEmployee.odoo_id == odoo_id)
        .where(DimEmployee.valid_from <= moment)
        .where((DimEmployee.valid_to.is_(None)) | (DimEmployee.valid_to > moment))
    ).scalar_one_or_none()


def _valid_at(moment: datetime) -> list[Any]:
    """The half-open window predicate, shared so it is written once.

    `valid_from <= moment < valid_to`. Half-open on purpose: a person whose
    record changed at noon is counted exactly once by a query at noon, rather
    than twice or not at all.
    """
    return [
        DimEmployee.valid_from <= moment,
        (DimEmployee.valid_to.is_(None)) | (DimEmployee.valid_to > moment),
    ]


def known_entities(session: Session) -> list[str]:
    """Every company the platform has seen an employee belong to.

    Not the enrollable population — this is only the companies that appear
    among people who touched training. It exists to make Q-15 concrete: live
    data shows attendees spanning five companies while the workbook's
    denominator covered one, which is P-13, and this is the query that shows
    somebody the five.
    """
    rows = session.execute(
        select(DimEmployee.company_name)
        .where(DimEmployee.company_name.is_not(None))
        .distinct()
        .order_by(DimEmployee.company_name)
    ).scalars()
    # The `is_not(None)` is in the query; this repeats it for the type checker,
    # which cannot see a WHERE clause narrow a nullable column.
    return [name for name in rows if name is not None]


def headcount_as_of(
    session: Session,
    *,
    moment: datetime,
    entity_set: Collection[str] | None = None,
    active_only: bool = True,
) -> Headcount:
    """How many people the platform knew about at `moment` (FR-B02).

    The query Participation Rate is meant to divide by, and the direct
    replacement for the workbook's hardcoded 192 (P-01). Counting distinct
    `odoo_id` rather than rows, because a person mid-way through a versioning
    boundary must not be counted twice — the partial unique index makes that
    nearly impossible, and this makes it actually impossible.

    `entity_set` names the companies to count, and answering P-13 is its whole
    purpose: the workbook divided 128 attendees drawn from several companies by
    212 employees of one. Passing a set scopes the denominator; passing None
    counts everyone known, which is only ever correct if the numerator is
    unscoped too. Whichever is chosen, `Headcount.entity_set` records it, so an
    export can state the population it divided by instead of leaving a reader
    to assume.

    `active_only` filters on the *versioned* status, so a person who has since
    left still counts in a month when they had not yet. That is the entire
    reason the dimension is SCD Type 2.

    READ `covers_enrollable_population` BEFORE PUBLISHING THE RESULT. It is
    false today and the number is a floor, not a headcount — see `Headcount`.
    """
    conditions = _valid_at(moment)
    if active_only:
        conditions.append(func.lower(DimEmployee.status) == "active")

    entities = tuple(entity_set) if entity_set is not None else None
    if entities is not None:
        conditions.append(DimEmployee.company_name.in_(entities))

    count = session.execute(
        select(func.count(func.distinct(DimEmployee.odoo_id))).where(*conditions)
    ).scalar_one()

    # Estimated when any version being counted was backdated past this moment —
    # i.e. we are reporting on a date before we had ever looked at that person.
    # Asked of the same filtered set rather than of the table as a whole, so a
    # query about last week is not labelled estimated merely because some
    # unrelated person was first seen yesterday.
    estimated = session.execute(
        select(func.count())
        .select_from(DimEmployee)
        .where(*conditions, DimEmployee.is_estimated, DimEmployee.observed_from > moment)
    ).scalar_one()

    return Headcount(
        count=count,
        moment=moment,
        entity_set=entities,
        is_estimated=estimated > 0,
    )
