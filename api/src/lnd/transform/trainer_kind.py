"""What kind of thing a trainer name names.

Three kinds arrive in one free-text field on the session: a colleague, an
outside vendor, and a stand-in for "nobody recorded this". The scorecard has to
tell them apart, because ranking them together says something untrue — `L&D
Team` currently sits sixth by sessions delivered, above four named people, and
it is not a person.

WHY THIS IS A LIST AND NOT A RULE

The obvious implementation is a keyword rule: anything containing "team",
"academy", "institute" or "consulting" is not a colleague. It is wrong the
first time somebody called Nadia Akademi delivers a session, and wrong silently
— a real trainer's scorecard would simply be labelled a vendor and nobody would
know to check. A false positive here misattributes a named person's work.

So it is an explicit list of the strings actually observed in the source, and
an unrecognised name is a person. New spellings arrive rarely (fifteen trainers
after the alias merge, from 123 sessions) and arrive through the exception
queue: an unclassified vendor shows up on a scorecard as a person, which is
visible, rather than a named person showing up as a vendor, which is not.

The list is matched on the *normalised* spelling — case-folded, whitespace
collapsed — for the same reason the alias table is. `L&D Team` and `l&d team`
are one entry, not two.

When L&D want to reclassify one of these, this belongs in `app` as authored
enrichment alongside the trainer aliases. It is code today because there are
two entries and no screen to edit them from; the shape of the move is a table
with the same three columns.
"""

from __future__ import annotations

from lnd.transform.conform import normalise

#: Names that stand in for an unrecorded trainer rather than naming one.
PLACEHOLDER_NAMES = frozenset({"l&d team", "l & d team", "lnd team", "tbd", "unknown"})

#: Names that are organisations delivering under contract, not colleagues.
EXTERNAL_NAMES = frozenset({"belton academy"})


def classify(raw_name: str | None) -> tuple[bool, bool]:
    """`(is_placeholder, is_external)` for one observed trainer spelling.

    Both false is the default and the common case: an unrecognised name is a
    person. Both true is not possible — a placeholder names nobody, so it cannot
    also name a vendor — and the ordering here makes placeholder win rather than
    leaving the pair to be interpreted.
    """
    normalised = normalise(raw_name)
    if normalised is None:
        return False, False
    if normalised in PLACEHOLDER_NAMES:
        return True, False
    return False, normalised in EXTERNAL_NAMES


__all__ = ["EXTERNAL_NAMES", "PLACEHOLDER_NAMES", "classify"]
