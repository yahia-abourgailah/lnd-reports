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
    # The programs endpoint. One request returns a whole program tree.
    (Source.CRM, Entity.PROGRAM),
    # The roster, and with it the participation denominator. Declared against
    # the CRM, not the HRIS: `get_users` returns every active employee with
    # company, department, sector, position and job level, which is the whole
    # reason the HRIS left the plan.
    (Source.CRM, Entity.EMPLOYEE),
)

# SESSIONS, ENROLLMENTS, ATTENDANCE AND EVALUATIONS ARE ABSENT ON PURPOSE
#
# They are not missing. They arrive nested inside the program payload, so there
# is no separate request to make and no sync run to record — an attendance row
# is exactly as current as the program tree it came in, and asking "how fresh is
# attendance?" separately has no answer the CRM could give.
#
# Declared here they reported `never_synced` forever. Because never_synced
# outranks stale, that held the whole platform badge at its worst state while
# both real pulls were minutes old, and the freshness alert fired every
# evaluation against a condition no sync could ever satisfy. A badge that is
# always red is a badge nobody reads, which costs more than having no badge.
#
# This is the same fault as the (HRIS, EMPLOYEE) entry removed in week 3, four
# more times. The rule it teaches: this list is what the platform *fetches*, not
# what it *holds*. Whether those rows transformed correctly is the transform
# invariant's question, and it is asked per grain before every commit.

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
