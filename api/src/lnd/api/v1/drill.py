"""GET /v1/drill/{key} — the rows behind a number.

The guarantee that matters is in the response: `total` is the count of the rows
the metric was computed over, and it is produced from the metric's own declared
population rather than from a query written alongside it. A drill-through with
its own idea of the population would agree on the day it was written and drift
the first time a population rule was corrected — and it would drift silently,
because both numbers stay plausible.

That is not hypothetical. The workbook's figures were unauditable precisely
because the question was unreadable: you could see the result and not what it
counted, so a figure that looked wrong could only be argued about. Six of the
nine turned out to need checking.

Rows are capped and the cap is reported. A truncated list that looks complete
is worse than no list.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from lnd.api.v1.kpis import FilterParams, MetricOut, _out
from lnd.auth.dependencies import CurrentUser
from lnd.db import get_db
from lnd.metrics import drilldown, registry
from lnd.metrics.filters import UnsupportedFilter

router = APIRouter(prefix="/drill", tags=["drill"])

DbSession = Annotated[Session, Depends(get_db)]


class DrillResponse(BaseModel):
    metric_key: str
    #: The metric itself, so the drawer can show the figure above the rows it
    #: is made of without a second request — and so a mismatch between them is
    #: visible on one screen rather than across two.
    metric: MetricOut
    grain: str
    columns: list[str]
    rows: list[dict[str, Any]]
    #: Every row the metric counted, not just those returned.
    total: int
    returned: int
    truncated: bool
    filters_applied: str


@router.get("/{key}", response_model=DrillResponse)
def drill(
    key: str,
    session: DbSession,
    _user: CurrentUser,
    filters: FilterParams,
    limit: Annotated[int, Query(ge=1, le=drilldown.MAX_LIMIT)] = drilldown.DEFAULT_LIMIT,
) -> DrillResponse:
    try:
        opened = drilldown.rows_behind(key, session, filters, limit=limit)
        metric = registry.compute(key, session, filters)
    except registry.UnknownMetric as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except UnsupportedFilter as exc:
        # The same refusal the metric gives. Showing rows the number was not
        # computed over is a worse lie than showing none.
        raise HTTPException(status_code=422, detail=str(exc)) from None

    return DrillResponse(
        metric_key=key,
        metric=_out(metric),
        grain=opened.grain.value,
        columns=list(opened.columns),
        rows=[dict(row) for row in opened.rows],
        total=opened.total,
        returned=len(opened.rows),
        truncated=opened.truncated,
        filters_applied=opened.filters_applied,
    )
