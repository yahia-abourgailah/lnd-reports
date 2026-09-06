"""Column helpers shared by every schema.

`enum_column` was written twice before this module existed — once in `ops.py`
and once in the star schema — and two spellings of the same pattern is how one
of them ends up storing `CRM` while the other stores `crm`. There is now one.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Enum as SAEnum


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    """Persist an enum's values, not its member names.

    Without this SQLAlchemy would store `CRM` rather than `crm`, and the column
    in the database would not match the string the API and the logs use.
    """
    return [member.value for member in enum_cls]


def enum_column(enum_cls: type[StrEnum], name: str, *, length: int = 32) -> SAEnum:
    """A VARCHAR constrained to the enum's values by a CHECK.

    `native_enum=False` on purpose. A native PostgreSQL enum type is a nuisance
    to extend — `ALTER TYPE ... ADD VALUE` has transaction restrictions that sit
    badly with migrations running as one transactional one-shot container. This
    renders as `VARCHAR(n) CHECK (col IN (...))`, which gives the same guarantee
    and turns adding a value into an ordinary constraint swap. Migration 0005
    swapped two of them for exactly that reason and cost four lines.

    The Python side still types as the enum, so mypy rejects a wrong string
    before the database ever sees it.
    """
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        length=length,
        values_callable=_enum_values,
    )
