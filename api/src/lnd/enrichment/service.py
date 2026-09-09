"""Reading and writing the overlay, without ever mutating a row.

One module for all five tables because the rule is identical for each: the live
row is the one with `superseded_at IS NULL`, a change closes it and opens
another, and both stay. Five copies of that would be five chances to write the
one that updates in place.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import Select, select, update
from sqlalchemy.orm import Session

from lnd.models.app_ import (
    IdentityMapping,
    ProgramOverride,
    SurveyOptionScore,
    SurveyQuestionMap,
    TrainerAlias,
)


class OverlayKind(StrEnum):
    """Which overlay table. The API's path segment, and the audit's subject."""

    PROGRAM_OVERRIDE = "program_override"
    TRAINER_ALIAS = "trainer_alias"
    SURVEY_QUESTION = "survey_question"
    SURVEY_OPTION_SCORE = "survey_option_score"
    IDENTITY_MAPPING = "identity_mapping"


@dataclass(frozen=True)
class OverlayTable:
    """What one overlay table's key is, and which fields a person may set."""

    model: type[Any]
    #: The natural key. Two live rows may not share it — enforced by a partial
    #: unique index on `superseded_at IS NULL`, so superseded rows may repeat it
    #: freely and history accumulates without fighting the constraint.
    key: tuple[str, ...]
    #: What an author supplies. Deliberately excludes the audit columns: a
    #: caller cannot set `authored_by` to somebody else, or backdate a change.
    fields: tuple[str, ...]


TABLES: dict[OverlayKind, OverlayTable] = {
    OverlayKind.PROGRAM_OVERRIDE: OverlayTable(
        ProgramOverride, ("crm_program_id", "field"), ("value",)
    ),
    OverlayKind.TRAINER_ALIAS: OverlayTable(TrainerAlias, ("normalised_name",), ("trainer_key",)),
    OverlayKind.SURVEY_QUESTION: OverlayTable(
        SurveyQuestionMap,
        ("crm_survey_id", "crm_question_id"),
        ("question_title", "dimension", "scale_min", "scale_max"),
    ),
    OverlayKind.SURVEY_OPTION_SCORE: OverlayTable(
        SurveyOptionScore, ("crm_question_id", "crm_option_id"), ("option_value", "score")
    ),
    OverlayKind.IDENTITY_MAPPING: OverlayTable(
        IdentityMapping, ("source_odoo_id",), ("target_odoo_id",)
    ),
}


class EnrichmentConflict(ValueError):
    """The write would leave the overlay in a state nobody asked for."""


@dataclass(frozen=True)
class OverlayEntry:
    """One row of an overlay table, live or superseded."""

    id: int
    kind: OverlayKind
    key: dict[str, Any]
    values: dict[str, Any]
    authored_by: str
    authored_at: dt.datetime
    superseded_at: dt.datetime | None
    note: str | None

    @property
    def is_live(self) -> bool:
        return self.superseded_at is None


def _entry(kind: OverlayKind, row: Any) -> OverlayEntry:
    table = TABLES[kind]
    return OverlayEntry(
        id=row.id,
        kind=kind,
        key={name: getattr(row, name) for name in table.key},
        values={name: getattr(row, name) for name in table.fields},
        authored_by=row.authored_by,
        authored_at=row.authored_at,
        superseded_at=row.superseded_at,
        note=row.note,
    )


def coerce(kind: OverlayKind, name: str, given: Any) -> Any:
    """Put a value into the type its column holds, or refuse and say so.

    JSON has no integers-in-a-dropdown. A form sends `"77"` for a programme id
    because that is what an HTML `<select>` produces, and PostgreSQL answers
    `operator does not exist: integer = character varying` — which surfaced as a
    500 and told the person filling the form nothing at all.

    Coercing here rather than in the caller means every route, every form and
    every script gets the same behaviour. A value that genuinely cannot be
    converted raises `EnrichmentConflict`, which the API already turns into a
    422 with the message in it.
    """
    column = getattr(TABLES[kind].model, name, None)
    python_type: type[Any] | None = None
    if column is not None:
        try:
            python_type = column.type.python_type
        except NotImplementedError:  # pragma: no cover - enum columns, already str
            python_type = None

    if given is None or python_type is None or isinstance(given, python_type):
        return given
    # bool before int: `issubclass(bool, int)` is true, and `int(True)` is a
    # silent 1 in a column that meant something else.
    if python_type is bool or isinstance(given, bool):
        return given
    try:
        return python_type(given)
    except (TypeError, ValueError):
        raise EnrichmentConflict(
            f"{kind.value}.{name} takes {python_type.__name__}, not {given!r}"
        ) from None


def _coerced(kind: OverlayKind, fields: dict[str, Any]) -> dict[str, Any]:
    return {name: coerce(kind, name, value) for name, value in fields.items()}


def _matching(kind: OverlayKind, key: dict[str, Any]) -> Select[Any]:
    table = TABLES[kind]
    missing = set(table.key) - set(key)
    if missing:
        raise EnrichmentConflict(
            f"{kind.value} is keyed on {', '.join(table.key)}; missing {', '.join(sorted(missing))}"
        )
    statement = select(table.model)
    for name in table.key:
        statement = statement.where(getattr(table.model, name) == coerce(kind, name, key[name]))
    return statement


def live(session: Session, kind: OverlayKind) -> list[OverlayEntry]:
    """Every row currently in force for one table."""
    table = TABLES[kind]
    rows = session.scalars(
        select(table.model).where(table.model.superseded_at.is_(None)).order_by(table.model.id)
    ).all()
    return [_entry(kind, row) for row in rows]


def history(session: Session, kind: OverlayKind, key: dict[str, Any]) -> list[OverlayEntry]:
    """Every value this key has ever had, oldest first.

    The answer to "who changed this, when, and what was it before" — which is
    the whole reason nothing is updated in place.
    """
    table = TABLES[kind]
    rows = session.scalars(_matching(kind, key).order_by(table.model.id)).all()
    return [_entry(kind, row) for row in rows]


def author(
    session: Session,
    kind: OverlayKind,
    *,
    key: dict[str, Any],
    values: dict[str, Any],
    authored_by: str,
    note: str | None = None,
    now: dt.datetime | None = None,
) -> OverlayEntry:
    """Set a value, superseding whatever was there.

    `authored_by` is passed by the route from the signed-in session and is never
    read from the request body — an audit trail a caller can address to somebody
    else is not an audit trail.

    Setting the same value again is still a new row. It looks redundant and is
    not: somebody re-affirming a decision after a source change is a fact worth
    keeping, and suppressing it would mean the history shows a gap where the
    review happened.
    """
    table = TABLES[kind]
    unknown = set(values) - set(table.fields)
    if unknown:
        raise EnrichmentConflict(
            f"{kind.value} has no field(s) {', '.join(sorted(unknown))}; "
            f"it accepts {', '.join(table.fields)}"
        )
    missing = set(table.fields) - set(values)
    if missing:
        raise EnrichmentConflict(
            f"{kind.value} needs {', '.join(sorted(missing))} — a partial override would "
            "leave the other fields at whatever the superseded row happened to hold"
        )

    # Into the types the columns hold, once, before anything touches the
    # database. A `<select>` sends "77" for a programme id, and PostgreSQL
    # answers `operator does not exist: integer = character varying` — which
    # reached the person filling the form as a 500 and told them nothing.
    key = _coerced(kind, key)
    values = _coerced(kind, values)

    stamp = now or dt.datetime.now(dt.UTC)
    # Close first. The unique index is partial on `superseded_at IS NULL`, so
    # inserting before closing would collide with the row being replaced.
    session.execute(
        update(table.model)
        .where(*[getattr(table.model, name) == key[name] for name in table.key])
        .where(table.model.superseded_at.is_(None))
        .values(superseded_at=stamp)
    )

    row = table.model(**key, **values, authored_by=authored_by, note=note, authored_at=stamp)
    session.add(row)
    session.flush()
    return _entry(kind, row)


def retire(
    session: Session,
    kind: OverlayKind,
    *,
    key: dict[str, Any],
    authored_by: str,
    note: str | None = None,
    now: dt.datetime | None = None,
) -> OverlayEntry | None:
    """Withdraw an override so the source value applies again.

    Not a delete. The row is superseded and stays, because "we overrode this
    for three months and then stopped" is exactly the history somebody will
    need when a figure moves and nobody remembers why.

    Returns the row that was retired, or None if nothing was in force — which
    is not an error: retiring an override that is already gone is the state the
    caller asked for.
    """
    table = TABLES[kind]
    stamp = now or dt.datetime.now(dt.UTC)
    row = session.scalars(
        _matching(kind, key).where(table.model.superseded_at.is_(None))
    ).one_or_none()
    if row is None:
        return None

    row.superseded_at = stamp
    if note:
        # The reason a value was withdrawn belongs on the row being withdrawn.
        # Appended rather than replacing the original note, so why it was set
        # and why it was retired are both readable.
        row.note = f"{row.note}\n— retired: {note}" if row.note else f"retired: {note}"
    session.flush()
    return _entry(kind, row)
