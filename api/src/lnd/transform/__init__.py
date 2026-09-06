"""raw -> core. The transform, and everything it needs to be honest about.

`core` is a pure function of (`raw` + the `app` overlay). Nothing in this
package calls a source system: every input is read back from
`raw.source_record` through `ingest.landing.current()`, which is what makes the
whole model replayable. A transform bug is fixed by changing code here and
re-running — never by re-querying the CRM, and never by editing a number.

    conform.py    unlike spellings of one thing, made one thing (P-04, P-05)
    dates.py      the calendar, generated rather than synced
    identity.py   attendee -> employee, by four probes in order (FR-B04)
    exceptions.py the queue: counted or excepted, never neither (FR-F05)
    employees.py  the SCD Type 2 loader (FR-B02)
    programs.py   the shredder: one nested program -> three fact grains
    invariant.py  the same promise, asserted: offered == written + refused
    runner.py     orchestration, and the Celery task

`exceptions.py` and `invariant.py` are the two halves of one guarantee and
neither is sufficient alone. The queue makes an exclusion *visible*; the
invariant makes an unexplained exclusion *impossible*, by refusing to commit a
pass whose output does not account for its input. A record can be missing from
a metric. It cannot be missing from both the metric and the ledger.

THE ORDER IS NOT ARBITRARY

Dimensions before facts, and among the dimensions, employees before programs:
a fact row cannot point at an employee version that does not exist yet, and
resolving an attendee's identity is a lookup against `dim_employee` as it
stands *after* this pass has loaded it. Running programs first would resolve
every attendee against yesterday's roster and quarantine everyone who first
appeared today.
"""

from __future__ import annotations
