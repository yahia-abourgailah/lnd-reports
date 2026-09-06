"""Attendee to employee, by four probes in order (FR-B04, FR-B05).

The workbook joined attendance to the employee roster on employee code, and
when the code was absent the row silently disappeared from every breakdown that
needed a person — 38 of them (P-07). This module is the replacement, and the
two rules it exists to enforce are:

    resolve in a stated order, and record which probe succeeded
    never drop an unresolved record; quarantine it and say so

WHY THE PROBE THAT SUCCEEDED IS STORED

`IdentityStatus` goes on every fact row. A match on employee code is a strong
claim; a match on normalised name is a guess that happens to be usually right.
Storing which one was used means the platform can answer "how confident are we
in this person's training history?" and, later, that a reviewer can filter the
weakest matches for a look. Deriving it at query time is impossible — by then
the evidence is gone.

WHY THERE IS NO FUZZY MATCHING

Levenshtein distance over 212 names would merge two real people eventually, and
when it did, nothing would show that it had. One person's training record would
absorb another's with no exception raised, because from the platform's point of
view nothing went wrong. An unresolved attendee is a visible, fixable gap; a
wrongly-resolved one is an invisible, permanent error. So the probes are exact
matches over normalised values, and anything they cannot settle goes to a human
through `app.identity_mapping`, which is consulted here as a *fourth probe*
rather than applied afterwards — so a mapped person's whole history rebuilds
correctly on the next pass instead of needing a patch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from lnd.models.app_ import IdentityMapping
from lnd.models.core import DimEmployee, IdentityStatus
from lnd.transform.conform import normalise, trim

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Resolution:
    """Who this is, and how sure we are.

    `employee_key` is None exactly when `status` is UNRESOLVED, so a caller
    cannot write a fact row that claims a resolution it does not have.
    """

    employee_key: int | None
    status: IdentityStatus

    @property
    def resolved(self) -> bool:
        return self.employee_key is not None


UNRESOLVED = Resolution(employee_key=None, status=IdentityStatus.UNRESOLVED)


@dataclass
class IdentityResolver:
    """The four probes, over an index built once per pass.

    Built once rather than queried per attendee on purpose. There are roughly
    400 attendance rows against a few hundred employees; four indexed queries
    each would be 1,600 round trips to save a few hundred kilobytes of Python
    dictionary. At this data volume the whole current dimension fits in memory
    comfortably, and NFR-01's two-second budget is easier to hold with one
    query than with sixteen hundred.
    """

    by_odoo_id: dict[str, int]
    by_employee_code: dict[str, int]
    by_email: dict[str, int]
    by_normalised_name: dict[str, int]
    manual: dict[str, str]

    @classmethod
    def build(cls, session: Session) -> IdentityResolver:
        """Index the *current* version of every employee, plus the manual map."""
        rows = session.execute(
            select(
                DimEmployee.employee_key,
                DimEmployee.odoo_id,
                DimEmployee.employee_code,
                DimEmployee.email,
                DimEmployee.normalised_name,
            ).where(DimEmployee.is_current)
        ).all()

        by_odoo_id: dict[str, int] = {}
        by_employee_code: dict[str, int] = {}
        by_email: dict[str, int] = {}
        by_name: dict[str, int] = {}

        # An ambiguous key is worse than a missing one: two people sharing a
        # normalised name means matching on it would attribute one person's
        # attendance to the other, with nothing to show for it. So a key seen
        # twice is *removed* rather than allowed to resolve to whichever row
        # happened to be read second, and the attendee falls through to the
        # next probe or to the exception queue.
        ambiguous: dict[str, set[str]] = {"code": set(), "email": set(), "name": set()}

        for employee_key, odoo_id, code, email, name in rows:
            by_odoo_id[odoo_id] = employee_key
            for value, index, label in (
                (trim(code), by_employee_code, "code"),
                (normalise(email), by_email, "email"),
                (name, by_name, "name"),
            ):
                if value is None:
                    continue
                if value in index and index[value] != employee_key:
                    ambiguous[label].add(value)
                index[value] = employee_key

        for label, index in (
            ("code", by_employee_code),
            ("email", by_email),
            ("name", by_name),
        ):
            for value in ambiguous[label]:
                index.pop(value, None)
                log.warning(
                    "identity key is ambiguous and will not be matched on",
                    extra={
                        "event": "transform.identity.ambiguous",
                        "probe": label,
                        "value": value,
                    },
                )

        manual: dict[str, str] = dict(
            session.execute(
                select(IdentityMapping.source_odoo_id, IdentityMapping.target_odoo_id).where(
                    IdentityMapping.superseded_at.is_(None)
                )
            ).all()  # type: ignore[arg-type]
        )

        return cls(
            by_odoo_id=by_odoo_id,
            by_employee_code=by_employee_code,
            by_email=by_email,
            by_normalised_name=by_name,
            manual=manual,
        )

    def resolve(
        self,
        *,
        odoo_id: str | None,
        employee_code: str | None = None,
        email: str | None = None,
        full_name: str | None = None,
    ) -> Resolution:
        """Probe in order and return the first hit, or UNRESOLVED.

        The order is the BRD's, with `odoo_id` ahead of it: that is the CRM's
        own cross-system identifier and the only key present on every
        attendance row, so trying anything else first would be answering an
        easier question than the one asked.

        The manual mapping is consulted after the automatic probes rather than
        before, so that an operator's mapping quietly retires the moment the
        source starts supplying the record itself — the same shape as every
        other override in this codebase.
        """
        if odoo_id:
            key = self.by_odoo_id.get(odoo_id)
            if key is not None:
                return Resolution(key, IdentityStatus.ODOO_ID)

        code = trim(employee_code)
        if code:
            key = self.by_employee_code.get(code)
            if key is not None:
                return Resolution(key, IdentityStatus.EMPLOYEE_CODE)

        normalised_email = normalise(email)
        if normalised_email:
            key = self.by_email.get(normalised_email)
            if key is not None:
                return Resolution(key, IdentityStatus.EMAIL)

        normalised_name = normalise(full_name)
        if normalised_name:
            key = self.by_normalised_name.get(normalised_name)
            if key is not None:
                return Resolution(key, IdentityStatus.NORMALISED_NAME)

        # Fourth probe: a person said these two are the same. Resolved *as*
        # the probe it lands on, not as a fifth status — the mapping says who
        # they are, and `odoo_id` is how we then know them.
        if odoo_id:
            target = self.manual.get(odoo_id)
            if target is not None:
                key = self.by_odoo_id.get(target)
                if key is not None:
                    return Resolution(key, IdentityStatus.ODOO_ID)

        return UNRESOLVED
