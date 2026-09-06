"""Put the frozen dataset through the real pipeline.

One function, used by both the golden generator and the golden test, so the
figures being pinned and the figures being checked are produced by identical
code. Two implementations of "load the reference dataset" would eventually
disagree, and the disagreement would look like a regression.

It lands into `raw` and runs the ordinary transform rather than writing `core`
directly. That is the whole value: the frozen payloads enter the pipeline where
the CRM's own payloads do, so validation, the shred into three grains, identity
resolution, SCD versioning, conforming, the exception rules and the transform
invariant are all inside what the golden values measure. Writing `core` from
the fixture would pin the schema and test none of the code that fills it.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from lnd.ingest.landing import land
from lnd.ingest.models import Entity, Source
from lnd.reference.freeze import load as load_dataset
from lnd.transform.runner import TransformResult, transform_programs

log = logging.getLogger(__name__)


def replay(session: Session, dataset: dict[str, Any] | None = None) -> TransformResult:
    """Land the frozen dataset and transform it. Returns what the pass did.

    The roster is landed as well as the programs, because the two populations
    are the point: 1,428 people the CRM calls staff against the 419 who have
    trained, and `on_current_roster` telling them apart. A replay that landed
    only programs would produce a participation denominator that cannot be
    published, and every coverage figure here would be pinned at the wrong
    value.
    """
    data = dataset if dataset is not None else load_dataset()

    land(
        session,
        source=Source.CRM,
        entity=Entity.PROGRAM,
        records=[(str(program["id"]), program) for program in data["programs"]],
    )
    land(
        session,
        source=Source.CRM,
        entity=Entity.EMPLOYEE,
        records=[(str(user["odoo_id"]), user) for user in data["roster"]],
    )

    result = transform_programs(session)
    log.info(
        "replayed the reference dataset",
        extra={
            "event": "reference.replayed",
            "programs": result.programs_transformed,
            "attendance": result.attendance,
        },
    )
    return result
