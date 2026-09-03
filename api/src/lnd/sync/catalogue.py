"""What the platform is supposed to be syncing.

Freshness needs this. Reporting only the pairs found in `sync_run` would make
the worst case — an entity that has never synced at all — the one case the
endpoint stays silent about, because absence of history would read as absence of
a problem.

So the answer is the union of what is declared here and what is observed in
history: a declared pair with no runs reports `never_synced`, and a pair that
appears in history without being declared is still reported rather than hidden.
That second half matters while the catalogue is incomplete, which it is.

Deliberately not configuration. Which entities exist is a fact about the
platform, changes with a code release, and belongs where it can be tested.
"""

from __future__ import annotations

from lnd.models import SyncEntity, SyncSource

# Pairs whose source is settled.
#
# Not listed yet, and why:
#   (?, FEEDBACK)            Q-03 has not settled whether evaluations come from
#                            the CRM or from Microsoft Forms. Adding the wrong
#                            one would report a permanent never_synced against
#                            a source that was never meant to carry it.
#   (LINKEDIN, COURSE_...)   Scope is unresolved — the delivery plan (BRD v1.0)
#                            puts LinkedIn Learning out of v1 for want of a
#                            feed; the repository README follows v1.1 and lists
#                            it as a source.
#
# Both are still valid values on `sync_run`, so if either starts syncing it
# appears in freshness through the observed half of the union.
EXPECTED_ENTITIES: tuple[tuple[SyncSource, SyncEntity], ...] = (
    (SyncSource.CRM, SyncEntity.PROGRAM),
    (SyncSource.CRM, SyncEntity.SESSION),
    (SyncSource.CRM, SyncEntity.ENROLLMENT),
    (SyncSource.CRM, SyncEntity.ATTENDANCE),
    (SyncSource.HRIS, SyncEntity.EMPLOYEE),
)

_SOURCE_ORDER = {member: index for index, member in enumerate(SyncSource)}
_ENTITY_ORDER = {member: index for index, member in enumerate(SyncEntity)}


def ordering_key(pair: tuple[SyncSource, SyncEntity]) -> tuple[int, int]:
    """Sort by declaration order, not alphabetically.

    The enums are declared in pipeline order — programs before sessions before
    enrollments before attendance — which is the order someone reading a
    freshness report expects to see them in.
    """
    source, entity = pair
    return (_SOURCE_ORDER[source], _ENTITY_ORDER[entity])
