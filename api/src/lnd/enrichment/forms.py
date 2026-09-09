"""What each overlay form asks for, in words a non-programmer can answer.

The enrichment screen used to render one generic form for all five overlays: a
box for the column name, a box for the keys, and a box where you hand-wrote
JSON. It worked, and only for whoever built it. The person who actually needs it
is an L&D specialist looking at "Programme 77 has no trainer" and wanting to say
who the trainer was.

So the form is described here, on the server, beside the table it writes to.
Three things follow.

**The labels are words, not columns.** `crm_program_id` becomes "Programme", and
what the screen shows is the programme's title with its id beside it.

**The options come from the data.** Which programmes exist, which trainers, which
of the five dimensions a survey question can measure — all read live rather than
typed into the front end, where a list goes stale silently and the first symptom
is a value somebody cannot select.

**There is one description, not two.** The same `TABLES` map that decides what
`author()` will accept decides what the form offers, so a form cannot ask for a
field the write path refuses, and a field added to the write path cannot be
invisible on screen. `test_enrichment_forms` asserts the two agree.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from lnd.enrichment.service import TABLES, OverlayKind
from lnd.models.app_ import EnrichmentField
from lnd.models.core import DimProgram, DimTrainer, EvaluationDimension


class InputKind(StrEnum):
    """How the screen should render one field.

    Deliberately small. Every additional kind is a branch in the front end, and
    the failure this module exists to fix was a form too generic to be usable —
    not one too plain.
    """

    TEXT = "text"
    NUMBER = "number"
    #: A closed list, supplied in `options`.
    SELECT = "select"


@dataclass(frozen=True)
class Option:
    value: str
    label: str


@dataclass(frozen=True)
class FormField:
    """One question the form asks."""

    name: str
    label: str
    input: InputKind
    #: Under the label. Says what the answer is for, in the terms of somebody
    #: who has just come from an exception rather than from the schema.
    help: str = ""
    options: tuple[Option, ...] = ()
    placeholder: str = ""
    required: bool = True


@dataclass(frozen=True)
class OverlayForm:
    kind: OverlayKind
    label: str
    #: What authoring one of these does, said once and shown above the form.
    purpose: str
    #: The natural key — which thing is being decided about.
    key: tuple[FormField, ...]
    #: What is being decided.
    values: tuple[FormField, ...]
    #: True where authoring many keys at one value is the ordinary case, as it
    #: is for trainer spellings. Everything else is one decision at a time.
    supports_bulk: bool = False
    fields_note: str = ""
    examples: tuple[str, ...] = field(default_factory=tuple)


def _programmes(session: Session) -> tuple[Option, ...]:
    """Every programme, newest first, labelled the way a person recognises one.

    Title and date rather than id, with the id kept in the label because the
    exception queue names programmes by id and somebody arriving from there is
    holding a number.
    """
    rows = session.execute(
        select(DimProgram.crm_program_id, DimProgram.title, DimProgram.start_date)
        .where(DimProgram.deleted_at_source.is_(None))
        .order_by(DimProgram.start_date.desc().nullslast(), DimProgram.crm_program_id.desc())
    ).all()
    return tuple(
        Option(
            value=str(program_id),
            label=f"#{program_id} · {title}" + (f" · {start:%b %Y}" if start else ""),
        )
        for program_id, title, start in rows
    )


def _trainers(session: Session) -> tuple[Option, ...]:
    """The people a spelling can resolve to.

    Placeholders and vendors are marked in the label rather than hidden: `L&D
    Team` is a real destination for a session nobody recorded, and somebody
    choosing it should know what they are choosing.
    """
    rows = session.execute(
        select(
            DimTrainer.trainer_key,
            DimTrainer.canonical_name,
            DimTrainer.is_placeholder,
            DimTrainer.is_external,
        ).order_by(DimTrainer.canonical_name)
    ).all()
    marks = {(True, False): " · not a person", (False, True): " · external vendor"}
    return tuple(
        Option(
            value=str(key),
            label=name + marks.get((placeholder, external), ""),
        )
        for key, name, placeholder, external in rows
    )


PROGRAM_FIELD_HELP: dict[EnrichmentField, str] = {
    EnrichmentField.TRAINER_NAME: "Who delivered it, when the CRM does not say",
    EnrichmentField.CUSTOMISED_DEPARTMENT: "Which department it was built for",
}


#: The forms themselves. Options are left empty here and filled by `forms()`
#: from live data — so this list is the single declaration of what each form
#: asks for, and the check at the bottom reads it rather than a copy.
_FORMS: tuple[OverlayForm, ...] = (
    OverlayForm(
        kind=OverlayKind.PROGRAM_OVERRIDE,
        label="Programme overrides",
        purpose=(
            "Supply something about a programme the CRM does not hold — most often "
            "who delivered it. The CRM keeps saying what it says; this wins when the "
            "figures are built."
        ),
        key=(
            FormField(
                name="crm_program_id",
                label="Programme",
                input=InputKind.SELECT,
                help="The one this decision is about.",
            ),
            FormField(
                name="field",
                label="What are you supplying?",
                input=InputKind.SELECT,
                options=tuple(
                    Option(
                        value=member.value,
                        label=member.value.replace("_", " ").capitalize(),
                    )
                    for member in EnrichmentField
                ),
                help="; ".join(
                    f"{member.value.replace('_', ' ')} — {text.lower()}"
                    for member, text in PROGRAM_FIELD_HELP.items()
                ),
            ),
        ),
        values=(
            FormField(
                name="value",
                label="The answer",
                input=InputKind.TEXT,
                help="Written exactly as it should appear in the figures.",
                placeholder="Amr Alaa",
            ),
        ),
        examples=("Programme 77 has no trainer on any session — name the person here.",),
    ),
    OverlayForm(
        kind=OverlayKind.TRAINER_ALIAS,
        label="Trainer spellings",
        purpose=(
            "Point a spelling at the person it means. The CRM stores the trainer as "
            "free text on each session — the only person in the payload without a "
            "key — so one man can appear twice under two spellings."
        ),
        key=(
            FormField(
                name="normalised_name",
                label="The spelling as the CRM has it",
                input=InputKind.TEXT,
                help="Lower case, spaces collapsed. Copy it from the trainer list.",
                placeholder="ahmed elshiaty",
            ),
        ),
        values=(
            FormField(
                name="trainer_key",
                label="Is really",
                input=InputKind.SELECT,
                help="The person that spelling should count towards.",
            ),
        ),
        supports_bulk=True,
        fields_note="Several spellings can point at one person in a single go.",
    ),
    OverlayForm(
        kind=OverlayKind.SURVEY_QUESTION,
        label="Survey questions",
        purpose=(
            "Say which of the five measured things a question asks about. Every "
            "programme has its own survey, so a new one arrives with each — and an "
            "unmapped question's answers are left out of the scores entirely."
        ),
        key=(
            FormField(
                name="crm_survey_id",
                label="Survey number",
                input=InputKind.NUMBER,
                help="From the exception, which names both numbers.",
            ),
            FormField(
                name="crm_question_id",
                label="Question number",
                input=InputKind.NUMBER,
            ),
        ),
        values=(
            FormField(
                name="dimension",
                label="What does it measure?",
                input=InputKind.SELECT,
                options=tuple(
                    Option(
                        value=member.value,
                        label=member.value.replace("_", " ").capitalize(),
                    )
                    for member in EvaluationDimension
                ),
            ),
            FormField(
                name="scale_min",
                label="Lowest score on the scale",
                input=InputKind.NUMBER,
                placeholder="1",
            ),
            FormField(
                name="scale_max",
                label="Highest score on the scale",
                input=InputKind.NUMBER,
                placeholder="5",
            ),
            FormField(
                name="question_title",
                label="The question, as written",
                input=InputKind.TEXT,
                help="Kept so the mapping can be checked later without the payload.",
                required=False,
            ),
        ),
    ),
    OverlayForm(
        kind=OverlayKind.SURVEY_OPTION_SCORE,
        label="Survey answer options",
        purpose=(
            "Give a written answer its number. A question mapped to a dimension can "
            "still offer an option the platform cannot score, and those responses "
            "are left out of that score."
        ),
        key=(
            FormField(
                name="crm_question_id",
                label="Question number",
                input=InputKind.NUMBER,
            ),
            FormField(
                name="crm_option_id",
                label="Option number",
                input=InputKind.NUMBER,
            ),
        ),
        values=(
            FormField(
                name="option_value",
                label="The answer, as written",
                input=InputKind.TEXT,
                placeholder="Strongly agree",
            ),
            FormField(
                name="score",
                label="Its score",
                input=InputKind.NUMBER,
                help="On the question's own scale.",
                placeholder="5",
            ),
        ),
    ),
    OverlayForm(
        kind=OverlayKind.IDENTITY_MAPPING,
        label="Same person, two ids",
        purpose=(
            "Point an id the platform cannot place at the person it really is. "
            "Empty is the healthy state, and it is empty today."
        ),
        key=(
            FormField(
                name="source_odoo_id",
                label="The id that cannot be placed",
                input=InputKind.TEXT,
                help="Copied from the exception.",
            ),
        ),
        values=(
            FormField(
                name="target_odoo_id",
                label="The id of the person it is",
                input=InputKind.TEXT,
            ),
        ),
    ),
)


#: Which fields need a list read from the database, and where to get it.
_OPTIONS: dict[tuple[OverlayKind, str], Callable[[Session], tuple[Option, ...]]] = {
    (OverlayKind.PROGRAM_OVERRIDE, "crm_program_id"): _programmes,
    (OverlayKind.TRAINER_ALIAS, "trainer_key"): _trainers,
}


def forms(session: Session) -> list[OverlayForm]:
    """Every overlay form, with its live options filled in.

    Programmes and trainers are read now rather than declared above, because a
    list typed into the front end goes stale silently and the first symptom is
    a value somebody cannot select.
    """

    def fill(kind: OverlayKind, fields: tuple[FormField, ...]) -> tuple[FormField, ...]:
        return tuple(
            replace(item, options=_OPTIONS[(kind, item.name)](session))
            if (kind, item.name) in _OPTIONS
            else item
            for item in fields
        )

    return [
        replace(form, key=fill(form.kind, form.key), values=fill(form.kind, form.values))
        for form in _FORMS
    ]


def _check_forms() -> None:
    """Every form writes what the write path accepts, and nothing else.

    A form offering a field `author()` refuses is a form somebody fills in and
    cannot submit; a field the write path accepts and no form offers is one
    nobody can supply. Both are checked at import, where the failure names the
    kind rather than surfacing as a 422 in front of a user.
    """
    described = {form.kind: form for form in _FORMS}
    missing = [kind.value for kind in TABLES if kind not in described]
    if missing:
        raise RuntimeError(f"overlay kinds with no form: {missing}")

    for kind, table in TABLES.items():
        form = described[kind]
        keys = tuple(item.name for item in form.key)
        values = tuple(item.name for item in form.values)
        if set(keys) != set(table.key):
            raise RuntimeError(
                f"{kind.value}: the form asks for keys {sorted(keys)}, "
                f"the table's key is {sorted(table.key)}"
            )
        if set(values) != set(table.fields):
            raise RuntimeError(
                f"{kind.value}: the form asks for values {sorted(values)}, "
                f"the table accepts {sorted(table.fields)}"
            )


_check_forms()

__all__ = ["FormField", "InputKind", "Option", "OverlayForm", "forms"]
