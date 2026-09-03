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

from lnd.models import Entity, Source

# Pairs whose source is settled.
#
# Evaluations are here now that Q-03 is answered: the CRM's Learning Program
# Dataset returns `survey`, `survey_answers[]` and `assessment_answers[]` nested
# inside each program, so feedback comes from the CRM and Microsoft Forms is not
# a source at all. That answer cost one tuple, because the grain was always
# (source, entity) rather than a column per source.
#
# Not listed yet:
#   (LINKEDIN, COURSE_COMPLETION)  In the enum because the source exists, but
#                                  no client does and v1 scope is unconfirmed —
#                                  the delivery plan puts LinkedIn Learning out
#                                  of v1 for want of a feed. Declaring it would
#                                  raise a permanent never_synced alert against
#                                  something nobody has agreed to build.
#
# It remains a valid value on `sync_run`, so if it ever starts syncing it
# appears in freshness through the observed half of the union.
EXPECTED_ENTITIES: tuple[tuple[Source, Entity], ...] = (
    (Source.CRM, Entity.PROGRAM),
    (Source.CRM, Entity.SESSION),
    (Source.CRM, Entity.ENROLLMENT),
    (Source.CRM, Entity.ATTENDANCE),
    (Source.CRM, Entity.EVALUATION),
    (Source.HRIS, Entity.EMPLOYEE),
)

_SOURCE_ORDER = {member: index for index, member in enumerate(Source)}
_ENTITY_ORDER = {member: index for index, member in enumerate(Entity)}


def ordering_key(pair: tuple[Source, Entity]) -> tuple[int, int]:
    """Sort by declaration order, not alphabetically.

    The enums are declared in pipeline order — programs before sessions before
    enrollments before attendance — which is the order someone reading a
    freshness report expects to see them in.
    """
    source, entity = pair
    return (_SOURCE_ORDER[source], _ENTITY_ORDER[entity])
