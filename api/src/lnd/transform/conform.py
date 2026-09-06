"""Normalising categoricals, once, on the way in.

Every function here is total: it returns a conformed value or it says why it
could not, and it never guesses. A conformer that quietly returned "Unknown"
would put a category nobody chose into the dimension and remove any chance of
noticing the source had changed.

The pattern is the same throughout. `conform_x` returns `(value, problem)`:
exactly one is set. The caller counts the value or quarantines the problem, and
because there is no third outcome, `received = counted + quarantined` holds by
construction rather than by discipline.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from lnd.models.app_ import QuarantineReason


@dataclass(frozen=True)
class Problem:
    """Why a value could not be conformed, in terms the queue groups by."""

    reason: QuarantineReason
    detail: str


def conform_optional_text(value: object) -> str | None:
    """Trim, collapse inner runs of whitespace, and treat empty as absent.

    Applied to every free-text categorical before it reaches a dimension. It is
    the single most valuable rule in this module, because whitespace damage is
    invisible in every tool a person would check with: `"Finance "` and
    `"Finance"` render identically in a spreadsheet, a psql result and a JSON
    dump, and group separately in every one of them.

    That is not hypothetical here. `user.sector` arrived with a trailing space
    on 940 of 1,052 records (P-05), turning 23 real sectors into 28. The CRM
    team has since fixed it at source — the live roster now measures zero — but
    the trim stays. A conformer that was removed because the source was fixed is
    a conformer that will be missing the next time the source regresses, and
    the cost of keeping it is one function call.

    NFKC first, so a non-breaking space and a full-width character conform to
    the ASCII forms everything else is compared against.
    """
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value))
    collapsed = " ".join(text.split())
    return collapsed or None


def conform_sector(value: object) -> tuple[str | None, Problem | None]:
    """A sector, or the reason it is not one.

    Absent is legitimate — not every employee record carries a sector — so a
    null returns cleanly rather than as a problem. What does not return cleanly
    is a value that is present but unusable, because that is a source change
    somebody needs to see.
    """
    conformed = conform_optional_text(value)
    if conformed is None:
        return None, None
    if len(conformed) > 128:
        return None, Problem(
            QuarantineReason.INVALID_VALUE,
            f"sector is {len(conformed)} characters, which is a payload shape change, not a sector",
        )
    return conformed, None


def conform_grade(value: object) -> tuple[int | None, Problem | None]:
    """Job level grade as an integer.

    The source sends it as text, which is why the workbook sorted `"10"` before
    `"9"`. Casting here means no query can re-derive it differently.

    Two spellings are in circulation and both are accepted: the API document
    describes `"G7"`, the live payload returns `"9"`. Accepting the documented
    form as well costs one line and means the transform does not break on the
    day the CRM starts sending what its own document promises.
    """
    if value is None:
        return None, None
    text = conform_optional_text(value)
    if text is None:
        return None, None

    digits = text[1:] if len(text) > 1 and text[0] in {"G", "g"} else text
    try:
        grade = int(digits)
    except ValueError:
        return None, Problem(
            QuarantineReason.INVALID_VALUE,
            f"job_level_grade {text!r} is not a number and not a G-prefixed grade",
        )
    # 0 is a sentinel, not a grade, and it is not an error either. Every one of
    # the 20 employees carrying it has `job_level_name = "Freelancer"`, and
    # every Freelancer carries it — they sit outside the internal ladder rather
    # than at the bottom of it. Null is the honest grade; `job_level_name` still
    # says what they are, so nothing is lost.
    #
    # Quarantining them, which is what this did first, was the more expensive
    # mistake: they are active employees, so removing them from the dimension
    # would have quietly cut 20 people out of the participation denominator —
    # the exact class of silent shortfall this pipeline exists to prevent.
    if grade == 0:
        return None, None
    if grade < 0:
        return None, Problem(
            QuarantineReason.INVALID_VALUE,
            f"job_level_grade {text!r} is negative, which no ladder has",
        )
    return grade, None
