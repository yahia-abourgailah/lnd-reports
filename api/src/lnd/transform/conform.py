"""Conformance: making unlike spellings of one thing into one thing (FR-B07).

This module is the fix for two of the workbook's defects, and both of them are
the same mistake made about different columns:

    P-05  `Projects` and `Projects ` became two rows in the sector pivot,
          because a trailing space is invisible and a GROUP BY is not.
          Measured on live data: 940 of 1,052 user objects carry a trailing
          space, and 28 distinct raw values collapse to 23 once trimmed. Five
          of the sectors in the workbook's breakdown do not exist.

    P-04  `Ahmed Nasr`, `ahmed nasr` and `A. Nasr` became three trainers, so
          one person's NPS was reported three times over three subsets of
          their own responses.

NORMALISE FOR MATCHING, DISPLAY WHAT ARRIVED

Every function here produces a *matching* key, and none of them produces a
value to show anyone. `normalise("Ahmed  NASR")` is `ahmed nasr`, which is
correct for deciding two strings are the same person and wrong for putting on a
scorecard. So the model stores both: `dim_trainer.normalised_name` beside
`canonical_name`, `dim_session.trainer_name_raw` beside `trainer_key`. When
somebody asks why two trainers merged, the evidence is still in the row.

WHAT NORMALISATION DELIBERATELY DOES NOT DO

It does not decide that `A. Nasr` and `Ahmed Nasr` are one person. Initials,
nicknames and transliterations are a judgement, and a fuzzy matcher that got it
right nine times in ten would silently merge two real people the tenth time —
a worse failure than the one it fixes, because nothing would show it happened.
Those merges are authored in `app.trainer_alias` by a person. This module only
removes differences that carry no information: case, accents, and whitespace.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from lnd.ingest.hashing import payload_hash

#: Runs of any whitespace, including the non-breaking spaces that arrive from
#: copy-paste into the CRM's own admin screens.
_WHITESPACE = re.compile(r"\s+")


def trim(value: str | None) -> str | None:
    """Strip surrounding whitespace, and treat an empty string as absent.

    The second half matters as much as the first. A `sector` of `""` and a
    `sector` of `None` mean the same thing — nobody recorded one — and if they
    are stored differently the coverage view grows an empty-string sector that
    nothing can be done about.
    """
    if value is None:
        return None
    stripped = _WHITESPACE.sub(" ", value).strip()
    return stripped or None


def normalise(value: str | None) -> str | None:
    """A matching key: accent-stripped, case-folded, whitespace-collapsed.

    `casefold` rather than `lower`, because it is the operation defined for
    comparison rather than for display, and the two differ for scripts this
    dataset will eventually contain.

    NFKD then dropping combining marks folds `Ahmèd` onto `ahmed`. That is the
    right call for a workforce whose names are transliterated inconsistently
    between systems: the accent is a property of one system's keyboard, not of
    the person.
    """
    trimmed = trim(value)
    if trimmed is None:
        return None
    decomposed = unicodedata.normalize("NFKD", trimmed)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return without_marks.casefold()


def attribute_hash(attributes: dict[str, Any]) -> str:
    """Fingerprint a dimension row's attributes, for change detection.

    The same function the raw layer uses on payloads, applied one layer up:
    it is what decides whether re-reading an unchanged person opens a new SCD
    version or does nothing at all. Using `payload_hash` rather than a second
    implementation means the two layers cannot disagree about whether
    something changed — and a second implementation is exactly how they would.

    Only the attributes are hashed. Never `valid_from`, never `transformed_at`:
    a hash that included the time would differ on every pass and open a new
    version of every employee every thirty minutes.
    """
    return payload_hash(attributes)
