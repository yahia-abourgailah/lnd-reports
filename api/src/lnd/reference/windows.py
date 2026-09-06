"""The periods the reconciliation compares over, named once.

Choosing the window is the substance of week 4 task 1, and it changes every
figure in the reconciliation statement. Naming both here means the golden suite
and the statement cannot disagree about what "now" was compared against.

WHY TWO WINDOWS AND NOT ONE

`WORKBOOK` is the honest comparison. The workbook covers February to August
2026 — 50 of the 123 sessions the CRM holds — so it is the only window in which
"the workbook said 24 programs and the platform says 55" is a statement about
*definitions* rather than about how much more data the platform can see.

`FULL` is what the platform will actually publish once it goes live, and it is
larger for reasons that have nothing to do with correctness. Presenting it
against the workbook's column invites the reading that the platform has
discovered enormous amounts of extra training, when most of the gap is simply
July 2025 to January 2026 and September 2026, which the workbook never covered.

Both are computed and both are pinned. The statement shows them side by side
and says which is which, because the difference between them is the difference
between "we corrected this" and "we can see more than you could".

WHAT THE DATES ARE

February to August 2026 inclusive, as month boundaries rather than the first and
last session dates. A window pinned to actual session dates would move whenever
a session is rescheduled, and a comparison window that moves is not a comparison.

    2026-02-01 .. 2026-08-31

HISTORY STARTS EARLIER THAN THE PLAN SAYS

Q-10 recorded history as beginning September 2025. It begins 2025-07-29, and 68
of 123 sessions fall in 2025 — too many to wave through as a rounding error in
somebody's recollection. Until L&D confirms those are real deliveries rather
than test data loaded during the CRM's own build, the full-dataset column
carries that caveat and the workbook window does not depend on the answer.
"""

from __future__ import annotations

from datetime import date

from lnd.metrics.filters import MetricFilters

#: The window the workbook covers, and the only like-for-like comparison.
WORKBOOK = MetricFilters(date_from=date(2026, 2, 1), date_to=date(2026, 8, 31))

#: Everything the CRM holds. What the platform publishes; not comparable to the
#: workbook's column without saying so.
FULL = MetricFilters()

#: What the reconciliation walks, in the order it presents them.
WINDOWS: tuple[tuple[str, MetricFilters], ...] = (
    ("workbook_window", WORKBOOK),
    ("full_dataset", FULL),
)

#: The claim task 1 verifies, so a change to the source's history is a failing
#: test rather than a surprise in a stakeholder meeting. Session dates are the
#: only history the platform has: `dim_employee` snapshots begin when the
#: platform first synced, which is why every earlier headcount is estimated.
EXPECTED_HISTORY_START = date(2025, 7, 29)
