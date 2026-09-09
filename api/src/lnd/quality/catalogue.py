"""One definition per data-quality rule, consumed identically everywhere.

The same argument the metric registry makes, applied to the other half of the
platform. A rule has a disposition, a sentence saying what it means, a sentence
saying what it costs, and — the part that makes the console useful rather than
merely honest — a suggested resolution naming the overlay that fixes it.

Those four facts were previously spread across three places: `DISPOSITIONS` in
the transform, a summary string written at the point the violation was raised,
and nothing at all for "what should somebody do about this". An operator faced
with `survey_option_unscored` and no further guidance is an operator who leaves
it in the queue.

WHY THE RESOLUTION NAMES AN OVERLAY RATHER THAN DESCRIBING ONE

Five of the eleven rules are fixed by authoring a row in `app`, and the
enrichment API already accepts exactly those five kinds. Naming the kind lets
the console hand somebody the form rather than a paragraph, and lets a test
assert that every rule claiming to be fixable is fixable through a route that
exists.

The other six cannot be fixed here at all, and say so plainly. A duplicate scan
is a fact about the CRM; a walk-in with no enrollment is a fact about how the
session ran. Offering an overlay for those would be offering to paper over the
source, which is the one thing this platform does not do.

WHAT "COSTS" MEANS

Whether the record is missing from a figure or present in it and merely flagged.
Only the first is a loss, and the exclusion banner spent a week saying otherwise
— reporting three flagged records as three excluded ones, which understates
coverage in the direction that sounds careful.
"""

from __future__ import annotations

from dataclasses import dataclass

from lnd.enrichment.service import OverlayKind
from lnd.models.ops import DqDisposition, DqRule


@dataclass(frozen=True)
class RuleSpec:
    """Everything the platform knows about one rule, in one place."""

    rule: DqRule
    title: str
    #: What the rule detects, in the words an operator reads in the console.
    means: str
    #: What it does to the numbers. The disposition in a sentence, because
    #: "quarantined" is a word this team uses and nobody else does.
    costs: str
    disposition: DqDisposition
    #: What to do about it. Always populated: "nothing here can fix this" is
    #: an answer, and a blank is not.
    resolution: str
    #: The overlay that fixes it, where one does. `None` means the fix is at
    #: source or the exception is a fact to be accepted rather than repaired.
    fixed_by: OverlayKind | None = None
    #: True where dismissal is the ordinary outcome rather than a last resort —
    #: a walk-in is not a defect, it is how the session ran.
    dismissal_is_normal: bool = False

    @property
    def costs_numbers(self) -> bool:
        return self.disposition is DqDisposition.QUARANTINED


RULES: dict[DqRule, RuleSpec] = {
    DqRule.IDENTITY_UNRESOLVED: RuleSpec(
        rule=DqRule.IDENTITY_UNRESOLVED,
        title="Person could not be identified",
        means=(
            "An attendance, enrollment or evaluation names somebody the platform cannot "
            "resolve to a person in the roster."
        ),
        costs=(
            "The row is written but joins to nobody, so it is missing from every figure "
            "that counts people and from every breakdown."
        ),
        disposition=DqDisposition.QUARANTINED,
        resolution=(
            "Map the unresolvable id to the person it really is. The next transform "
            "picks the mapping up and closes this itself."
        ),
        fixed_by=OverlayKind.IDENTITY_MAPPING,
    ),
    DqRule.DURATION_UNDERIVABLE: RuleSpec(
        rule=DqRule.DURATION_UNDERIVABLE,
        title="Session duration cannot be derived",
        means="A session carries no usable start and end time, so its length is unknown.",
        costs=(
            "The session is counted as a training day and contributes no hours, so "
            "Training Hours Delivered and Learner Hours are both short by it."
        ),
        disposition=DqDisposition.QUARANTINED,
        resolution=(
            "Correct the times in the CRM. Nothing here can invent a duration, and a "
            "guessed one would be indistinguishable from a measured one afterwards."
        ),
    ),
    DqRule.DUPLICATE_ATTENDANCE: RuleSpec(
        rule=DqRule.DUPLICATE_ATTENDANCE,
        title="The same person scanned twice for one session",
        means="Two attendance rows for one person on one session, which the grain forbids.",
        costs="The second row is dropped. Nothing is double-counted, and nothing is lost.",
        disposition=DqDisposition.QUARANTINED,
        resolution=(
            "Usually nothing: a double scan at the door is normal and the platform has "
            "already handled it. Dismiss it if the pair is genuinely one attendance."
        ),
        dismissal_is_normal=True,
    ),
    DqRule.SURVEY_QUESTION_UNMAPPED: RuleSpec(
        rule=DqRule.SURVEY_QUESTION_UNMAPPED,
        title="A survey question has no mapping",
        means=(
            "A programme's survey asks a scored question the platform cannot attribute "
            "to a metric. Surveys are per-programme, so a new one arrives with each."
        ),
        costs=(
            "Every answer to it is excluded from the quality scores and from NPS. The "
            "denominator shrinks with nothing on screen to say so — which is P-03."
        ),
        disposition=DqDisposition.QUARANTINED,
        resolution="Map the question to the dimension it measures, with its scale.",
        fixed_by=OverlayKind.SURVEY_QUESTION,
    ),
    DqRule.SURVEY_OPTION_UNSCORED: RuleSpec(
        rule=DqRule.SURVEY_OPTION_UNSCORED,
        title="A survey answer option has no score",
        means="A mapped question offers an option the platform cannot turn into a number.",
        costs="Responses choosing that option are excluded from the score they belong to.",
        disposition=DqDisposition.QUARANTINED,
        resolution="Give the option its numeric score on the question's scale.",
        fixed_by=OverlayKind.SURVEY_OPTION_SCORE,
    ),
    DqRule.TRAINER_MISSING: RuleSpec(
        rule=DqRule.TRAINER_MISSING,
        title="No trainer recorded on a session",
        means="A delivered session names nobody as its facilitator.",
        costs=(
            "Counted in full everywhere except the trainer breakdown, where it lands "
            "under the L&D Team placeholder rather than a person."
        ),
        disposition=DqDisposition.COUNTED,
        resolution="Set the trainer on the programme, or correct it in the CRM.",
        fixed_by=OverlayKind.PROGRAM_OVERRIDE,
    ),
    DqRule.CUSTOMISED_DEPT_MISSING: RuleSpec(
        rule=DqRule.CUSTOMISED_DEPT_MISSING,
        title="A customised programme names no department",
        means=("The CRM marks the programme as built for a department but does not say which one."),
        costs="Counted in full; absent from the department view of customised delivery.",
        disposition=DqDisposition.COUNTED,
        resolution="Record which department it was built for.",
        fixed_by=OverlayKind.PROGRAM_OVERRIDE,
    ),
    DqRule.ATTENDANCE_NO_ENROLLMENT: RuleSpec(
        rule=DqRule.ATTENDANCE_NO_ENROLLMENT,
        title="Attended without enrolling",
        means="Somebody attended a session of a programme they were never enrolled on.",
        costs=(
            "Counted in full. It is shown on the funnel as a walk-in rather than netted "
            "off, because subtracting it would cancel against a genuine no-show."
        ),
        disposition=DqDisposition.COUNTED,
        resolution=(
            "Nothing, ordinarily — a walk-in is how the session ran, not a defect. "
            "Dismiss it once you have satisfied yourself of that."
        ),
        dismissal_is_normal=True,
    ),
    DqRule.EVALUATION_NO_ATTENDANCE: RuleSpec(
        rule=DqRule.EVALUATION_NO_ATTENDANCE,
        title="Feedback from somebody with no attendance",
        means="A survey response exists for a person the attendance data never records.",
        costs=(
            "The response is counted in the quality scores and in NPS. It is the "
            "response rate's denominator it sits outside of."
        ),
        disposition=DqDisposition.COUNTED,
        resolution=(
            "Check whether the attendance was recorded at all. If the person really "
            "was there, the gap is in the scan; if not, the response is somebody "
            "else's and belongs in the CRM's hands."
        ),
    ),
    DqRule.CAPACITY_EXCEEDED: RuleSpec(
        rule=DqRule.CAPACITY_EXCEEDED,
        title="More enrollments than the declared capacity",
        means="A programme enrolled more people than the capacity it states.",
        costs=(
            "Counted in full. Fill Rate reads above 100% for that programme, which is "
            "the true answer rather than a clipped one."
        ),
        disposition=DqDisposition.COUNTED,
        resolution=(
            "Correct the capacity in the CRM if it was under-stated, or dismiss it if "
            "the room genuinely took more."
        ),
        dismissal_is_normal=True,
    ),
    DqRule.ATTENDEE_OUTSIDE_ROSTER: RuleSpec(
        rule=DqRule.ATTENDEE_OUTSIDE_ROSTER,
        title="Attendee is not in the programme's roster",
        means=(
            "Somebody attended a session without appearing in the programme's user "
            "list, so the payload carries no department, sector or job level for them."
        ),
        costs=(
            "Counted in Total Participants and in Learner Hours; absent from every "
            "coverage breakdown. This is P-07 arriving through a different door."
        ),
        disposition=DqDisposition.COUNTED,
        resolution=(
            "Ask the CRM team to add them to the programme's roster. Mapping them here "
            "would invent an attribute the source does not hold."
        ),
    ),
}

#: What the transform reads. Derived from the catalogue rather than declared
#: beside it: two lists of "which rules cost numbers" is how one of them comes
#: to disagree with the exclusion banner.
DISPOSITIONS: dict[DqRule, DqDisposition] = {rule: spec.disposition for rule, spec in RULES.items()}


def spec_for(rule: DqRule) -> RuleSpec:
    return RULES[rule]


def _check_catalogue() -> None:
    """Every rule is described. Run at import, like the metric registry's.

    A rule added to the enum and not here would reach the console as a bare
    identifier with no explanation and no suggested fix — visible, and useless.
    """
    missing = [rule.value for rule in DqRule if rule not in RULES]
    if missing:
        raise RuntimeError(f"data-quality rules with no catalogue entry: {missing}")


_check_catalogue()

__all__ = ["DISPOSITIONS", "RULES", "RuleSpec", "spec_for"]
