"""Migration 0006's enum values, checked against the models.

Migration 0006 spells its enum values out as literal tuples rather than
importing the enums, and the reason is in its docstring: a migration has to keep
saying what it said on the day it ran. Importing `DqRule` would make the
revision silently change meaning the next time somebody adds a member, and
replaying history would then produce a database that never existed.

The cost of that correctness is drift — the literals and the enums can diverge
with nothing to notice. This is what notices.

A failure here is not a licence to edit 0006. It means a model gained a value
the database does not allow, and the fix is a *new* migration swapping the CHECK
constraint, exactly as 0005 did — which is cheap only because these are
`VARCHAR + CHECK` rather than native PostgreSQL enum types.
"""

from __future__ import annotations

import importlib.util
from enum import StrEnum
from pathlib import Path
from types import ModuleType

import pytest

from lnd.models.app_ import EnrichmentField
from lnd.models.core import EvaluationDimension, IdentityStatus, NpsBand, ValueSource
from lnd.models.ops import DqDisposition, DqRule, DqStatus
from lnd.sources.crm.models import ProgramStatus, ProgramTarget, ProgramType

# Renumbered from 0006 on merge: the branch this arrived on was cut before
# 0006_source_presence existed and both claimed that id.
MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0007_core_model.py"


def load_migration() -> ModuleType:
    """Import 0006 by path — `alembic/versions` is not an importable package."""
    spec = importlib.util.spec_from_file_location("migration_0007", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PAIRS: list[tuple[str, type[StrEnum]]] = [
    ("VALUE_SOURCE", ValueSource),
    ("IDENTITY_STATUS", IdentityStatus),
    ("NPS_BAND", NpsBand),
    ("PROGRAM_STATUS", ProgramStatus),
    ("PROGRAM_TYPE", ProgramType),
    ("PROGRAM_TARGET", ProgramTarget),
    ("EVALUATION_DIMENSION", EvaluationDimension),
    ("ENRICHMENT_FIELD", EnrichmentField),
    ("DQ_RULE", DqRule),
    ("DQ_DISPOSITION", DqDisposition),
    ("DQ_STATUS", DqStatus),
]


@pytest.mark.parametrize(("literal_name", "enum_cls"), PAIRS, ids=[name for name, _ in PAIRS])
def test_migration_literals_match_the_models(literal_name: str, enum_cls: type[StrEnum]) -> None:
    literals = getattr(load_migration(), literal_name)
    assert tuple(literals) == tuple(member.value for member in enum_cls)


def test_every_enum_in_the_migration_is_covered_here() -> None:
    """So adding a twelfth enum to 0006 without a check fails rather than passes.

    Without this, the guard above protects exactly the enums somebody
    remembered to list, which is the same weakness it exists to remove.
    """
    module = load_migration()
    declared = {
        name
        for name, value in vars(module).items()
        if name.isupper() and isinstance(value, tuple) and all(isinstance(v, str) for v in value)
    }
    assert declared == {name for name, _ in PAIRS}
