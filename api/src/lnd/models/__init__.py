"""SQLAlchemy models.

Importing this package registers every table on `Base.metadata`. That is what
`alembic/env.py` consults, so a model that is never imported is a model Alembic
believes has been deleted.

Models are grouped by the schema they live in, and the grouping is the point:
`core` is derived and may be dropped and rebuilt at will, `app` is authored by
people and must never be, and `ops` records what the machinery did. A file per
schema means that reading one tells you which of those three kinds of data you
are looking at.

    ops.py    sync_run, alert_notification, dq_exception
    core.py   the star schema — dimensions and three facts
    app_.py   the enrichment overlay, superseded rather than updated
    columns.py  the one definition of the enum-as-CHECK column

`Source` and `Entity` are re-exported for convenience but *defined* in
`lnd.ingest.models`, beside the raw table that stores them. They are the
platform's shared vocabulary rather than anything specific to `ops`, and there
is exactly one definition of them on purpose.
"""

from __future__ import annotations

from lnd.ingest.models import Entity, RawRecord, Source
from lnd.models.app_ import (
    EnrichmentField,
    IdentityMapping,
    ProgramOverride,
    SurveyOptionScore,
    SurveyQuestionMap,
    TrainerAlias,
)
from lnd.models.core import (
    DimDate,
    DimEmployee,
    DimProgram,
    DimSession,
    DimTrainer,
    EvaluationDimension,
    FactAttendance,
    FactEnrollment,
    FactEvaluation,
    IdentityStatus,
    NpsBand,
    ValueSource,
)
from lnd.models.ops import (
    AlertKind,
    AlertNotification,
    AlertSeverity,
    DqDisposition,
    DqException,
    DqRule,
    DqStatus,
    SyncMode,
    SyncRun,
    SyncStatus,
    SyncTrigger,
)

__all__ = [
    "AlertKind",
    "AlertNotification",
    "AlertSeverity",
    "DimDate",
    "DimEmployee",
    "DimProgram",
    "DimSession",
    "DimTrainer",
    "DqDisposition",
    "DqException",
    "DqRule",
    "DqStatus",
    "EnrichmentField",
    "Entity",
    "EvaluationDimension",
    "FactAttendance",
    "FactEnrollment",
    "FactEvaluation",
    "IdentityMapping",
    "IdentityStatus",
    "NpsBand",
    "ProgramOverride",
    "RawRecord",
    "Source",
    "SurveyOptionScore",
    "SurveyQuestionMap",
    "SyncMode",
    "SyncRun",
    "SyncStatus",
    "SyncTrigger",
    "TrainerAlias",
    "ValueSource",
]
