"""The exception console: what could not be placed, and what to do about it.

FR-F01 to FR-F04. Everything the platform excluded from a figure is here, with
the rule that excluded it, what that costs, and — the part that makes this a
console rather than a list — the suggested resolution and a route that performs
it.

WHY EVERY ROW CARRIES ITS RULE'S DESCRIPTION

An operator meeting `survey_option_unscored` for the first time has no way to
know whether it matters. So the rule's meaning, its cost and its fix come from
`lnd.quality.catalogue` and travel with the row, exactly as a metric's
definition travels with its value. Nothing here writes those sentences: a
second copy would be a second thing to keep true, and the one on screen is the
one somebody would act on.

THREE WAYS TO RESOLVE, AND ONLY ONE OF THEM IS HERE

    map      author an identity mapping, alias or question map — enrichment
    override supply the value the CRM does not — enrichment
    dismiss  say "yes, and that is fine", with a reason — here

The first two are not reimplemented in this module. They are the enrichment API,
which already supersedes rather than updates and already records who decided
what; a second write path to the same tables would be a second audit trail. So a
row that is fixable by authoring names the overlay kind and the key to author,
and the console sends the person to that form. What arrives back is not a closed
exception — it is a *fixed one*: the next transform finds the rule satisfied and
closes the row itself, which is the only way a queue can be a picture of the
present rather than a list of things somebody remembered to tick off.

Dismissal is the exception, and is the one write here. It is a person saying
something no amount of re-reading the payload can contradict, so a pass never
reopens it.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lnd.api.v1.kpis import Envelope, FilterParams, _envelope
from lnd.auth.dependencies import CurrentUser
from lnd.db import get_db
from lnd.models.ops import DqDisposition, DqException, DqRule, DqStatus
from lnd.quality import catalogue
from lnd.quality import completeness as quality
from lnd.transform.exceptions import dismiss

router = APIRouter(prefix="/exceptions", tags=["exceptions"])

DbSession = Annotated[Session, Depends(get_db)]


# --------------------------------------------------------------------- shapes
class RuleOut(BaseModel):
    """One rule, described. The same four sentences everywhere it appears."""

    rule: str
    title: str
    means: str
    costs: str
    disposition: str
    #: True where the records are missing from figures rather than flagged in
    #: them. The distinction the exclusion banner got wrong for a week.
    costs_numbers: bool
    resolution: str
    #: The enrichment overlay that fixes it, where one does. Null means the fix
    #: is at source, or that the exception is a fact to accept rather than
    #: repair.
    fixed_by: str | None
    #: True where dismissal is the ordinary outcome, not a last resort.
    dismissal_is_normal: bool


class ExceptionOut(BaseModel):
    exception_key: str
    rule: str
    disposition: str
    status: str
    summary: str
    details: dict[str, object] | None
    crm_program_id: int | None
    crm_session_id: int | None
    employee_odoo_id: str | None
    first_seen_at: dt.datetime
    last_seen_at: dt.datetime
    #: How many passes have found this same violation. A high count on a young
    #: row means every half hour since it appeared, not that it happened often.
    occurrences: int
    #: Whole days since it was first raised. Age is what turns a queue into a
    #: backlog, and it is the column an operator sorts on.
    age_days: int


class RuleGroup(BaseModel):
    """Every open exception for one rule, under the rule's own description."""

    rule: RuleOut
    open_count: int
    oldest_days: int
    exceptions: list[ExceptionOut]


class ExceptionsResponse(Envelope):
    groups: list[RuleGroup]
    total_open: int
    #: Split the same way the banner splits it, from the same function.
    excluded: int
    flagged: int
    #: Open exceptions whose programme is not in `core`, so they belong to no
    #: period. Stated rather than dropped.
    unplaceable: int


class PeriodOut(BaseModel):
    period: str
    excluded: int
    flagged: int


class RuleCountOut(BaseModel):
    """A rule and how many open exceptions it holds in the scope asked about."""

    rule: RuleOut
    count: int


class CompletenessResponse(Envelope):
    excluded: int
    flagged: int
    unplaceable: int
    by_rule: list[RuleCountOut]
    months: list[PeriodOut]


class DismissRequest(BaseModel):
    #: Required, never optional. An exception dismissed with no stated reason
    #: is indistinguishable six months later from one dismissed by mistake.
    reason: Annotated[str, Field(min_length=3, max_length=1000)]

    @field_validator("reason")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        trimmed = value.strip()
        if len(trimmed) < 3:
            raise ValueError("say why — the reason is the whole point of a dismissal")
        return trimmed


def _rule_out(rule: DqRule) -> RuleOut:
    spec = catalogue.spec_for(rule)
    return RuleOut(
        rule=rule.value,
        title=spec.title,
        means=spec.means,
        costs=spec.costs,
        disposition=spec.disposition.value,
        costs_numbers=spec.costs_numbers,
        resolution=spec.resolution,
        fixed_by=spec.fixed_by.value if spec.fixed_by else None,
        dismissal_is_normal=spec.dismissal_is_normal,
    )


def _exception_out(row: DqException, *, today: dt.date) -> ExceptionOut:
    return ExceptionOut(
        exception_key=row.exception_key,
        rule=row.rule.value,
        disposition=row.disposition.value,
        status=row.status.value,
        summary=row.summary,
        details=row.details,
        crm_program_id=row.crm_program_id,
        crm_session_id=row.crm_session_id,
        employee_odoo_id=row.employee_odoo_id,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        occurrences=row.occurrences,
        age_days=(today - row.first_seen_at.date()).days,
    )


# --------------------------------------------------------------------- routes
@router.get("", response_model=ExceptionsResponse)
def list_exceptions(
    _user: CurrentUser,
    session: DbSession,
    filters: FilterParams,
    rule: Annotated[DqRule | None, Query(description="one rule only")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> ExceptionsResponse:
    """The open queue, grouped by rule, losses first.

    Ordered so the rules that cost figures are met before the ones that do not,
    then by how many rows each holds. A queue sorted alphabetically buries the
    five unresolved identities under eleven capacity notices.
    """
    statement = (
        select(DqException)
        .where(DqException.status == DqStatus.OPEN)
        .order_by(DqException.first_seen_at)
        .limit(limit)
    )
    if rule is not None:
        statement = statement.where(DqException.rule == rule)

    today = dt.date.today()
    rows = list(session.scalars(statement).all())

    grouped: dict[DqRule, list[DqException]] = {}
    for row in rows:
        grouped.setdefault(row.rule, []).append(row)

    scoped = quality.completeness(session)
    groups = [
        RuleGroup(
            rule=_rule_out(key),
            open_count=len(members),
            oldest_days=max((today - member.first_seen_at.date()).days for member in members),
            exceptions=[_exception_out(member, today=today) for member in members],
        )
        for key, members in grouped.items()
    ]
    groups.sort(
        key=lambda group: (
            not group.rule.costs_numbers,
            -group.open_count,
            group.rule.rule,
        )
    )

    return ExceptionsResponse(
        groups=groups,
        total_open=len(rows),
        excluded=scoped.excluded,
        flagged=scoped.flagged,
        unplaceable=scoped.unplaceable,
        **_envelope(session, filters, was_cached=False),
    )


@router.get("/rules", response_model=list[RuleOut])
def list_rules(_user: CurrentUser) -> list[RuleOut]:
    """Every rule the platform can detect, whether or not it is firing.

    A console that lists only what is currently broken cannot answer "what does
    this platform check for?", which is the question somebody asks before they
    trust a figure.
    """
    return [_rule_out(rule) for rule in DqRule]


@router.get("/completeness", response_model=CompletenessResponse)
def completeness(
    _user: CurrentUser, session: DbSession, filters: FilterParams
) -> CompletenessResponse:
    """How much of each period is actually in the figures (FR-F04).

    The months come from the data rather than from a range somebody typed: a
    trend over months with no programmes would draw a run of zeroes that read
    as a collapse in delivery rather than an absence of it.
    """
    scoped = quality.completeness(session, filters)
    series = quality.trend(session, filters)
    return CompletenessResponse(
        excluded=scoped.excluded,
        flagged=scoped.flagged,
        unplaceable=scoped.unplaceable,
        by_rule=[
            RuleCountOut(rule=_rule_out(entry.rule), count=entry.count) for entry in scoped.by_rule
        ],
        months=[
            PeriodOut(period=month.period, excluded=month.excluded, flagged=month.flagged)
            for month in series.months
        ],
        **_envelope(session, filters, was_cached=False),
    )


@router.post("/{exception_key:path}/dismiss", response_model=ExceptionOut)
def dismiss_exception(
    exception_key: str, request: DismissRequest, user: CurrentUser, session: DbSession
) -> ExceptionOut:
    """Record a deliberate "yes, and that is fine" (FR-F03).

    `:path` because an exception key contains colons — `capacity_exceeded:83` —
    and a plain segment would stop at the first one.

    The author comes from the session and is never read from the request body.
    Dismissal is the one resolution a pass will not undo, so it is the one that
    most needs a name against it.
    """
    if not dismiss(
        session,
        exception_key=exception_key,
        dismissed_by=user.email,
        reason=request.reason,
    ):
        raise HTTPException(status_code=404, detail="No such exception.")

    # `get_db` commits nothing — read paths must not — so every write route in
    # this API commits for itself.
    session.commit()
    row = session.scalar(select(DqException).where(DqException.exception_key == exception_key))
    assert row is not None
    return _exception_out(row, today=dt.date.today())


@router.get("/summary")
def summary(_user: CurrentUser, session: DbSession) -> dict[str, object]:
    """One line for the shell: how many, and how many of them cost figures."""
    scoped = quality.completeness(session)
    oldest = session.scalar(
        select(func.min(DqException.first_seen_at)).where(
            DqException.status == DqStatus.OPEN,
            DqException.disposition == DqDisposition.QUARANTINED,
        )
    )
    return {
        "open": scoped.total,
        "excluded": scoped.excluded,
        "flagged": scoped.flagged,
        "oldest_excluded_at": oldest.isoformat() if oldest else None,
    }


__all__ = ["router"]
