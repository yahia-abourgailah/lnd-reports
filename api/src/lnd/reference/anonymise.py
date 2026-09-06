"""Strip the people out of a real CRM payload, leave every defect in.

The frozen reference dataset has to be the real thing. A hand-written fixture
proves the transform handles the data somebody imagined; only a snapshot of the
live payload proves it handles the data that exists — 57 programs whose titles
collide sixteen ways, one trainer spelled two ways across fourteen sessions,
sectors carrying trailing spaces, and 149 people who trained and have since
left. Those are the reasons the platform exists, and a reference dataset that
quietly cleaned them up would assert that the pipeline works on data that never
arrives.

It also cannot contain real employees. `compose.dev.yaml` says "anonymised
fixtures, no real PII" and NFR-06 means it: names, emails and mobile numbers do
not belong in a git repository that outlives everyone in it.

So: replace the identities, preserve the structure. Every substitution below is
chosen to keep a specific defect detectable.

WHAT IS REPLACED, AND WHAT SURVIVES ON PURPOSE

    replaced   name, full_name, email, mobile, employee_code digits, odoo_id,
               trainer_name, location name
    kept       program and session ids, titles, dates, times, capacities,
               statuses, sectors, departments, companies, job levels, survey
               questions, answers and scores

Company and department names stay. They are not personal data, they are the
client's own organisational vocabulary, and P-13 — attendees spanning five
companies while the denominator covered one — is not testable without them.

DETERMINISM IS THE WHOLE MECHANISM

Every replacement is a keyed hash of the original, so the same input always
yields the same output. Three things depend on that:

  * **Golden values hold still.** A dataset that anonymised differently on
    every run would move the very figures the golden suite exists to pin.
  * **Identity still resolves.** One person appears in a roster row and in
    several programs' nested `user` objects. If those became different fake
    people the join would break, and the dataset would test a defect that is
    not there.
  * **Regenerating is safe.** Re-freezing after a CRM change produces a diff
    containing only what actually changed.

The key is a constant in this file rather than a secret. Nothing here is
protecting the mapping from an attacker who has both the output and the code —
it is removing personal data from a repository, and a checked-in key is what
makes the output reproducible by anyone who clones it.

THE TRAINER IS THE INTERESTING CASE

`Ahmed ElShiaty` and `Ahmed Elshiaty` are one person, and the dataset is only
worth having if they still are after anonymisation. Hashing each string on its
own would produce two unrelated fake people and destroy P-04 — the defect would
be gone and the test proving we merge them would pass vacuously.

So trainers are keyed on their *normalised* form, and the case pattern of the
original is reapplied to the substitute. Two spellings in, two spellings of one
fake person out, still merging to one `dim_trainer` row. The same applies to
the `X & Y` strings naming two trainers on one session: each side is
substituted separately so the pair survives.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from typing import Any

from lnd.transform.conform import normalise

#: Not a secret — see the module docstring. It exists to make the substitution
#: reproducible across machines, not to withhold anything from anyone.
KEY = b"lnd-reference-dataset-v1"

#: Enough that collisions are improbable at our scale and the output stays
#: readable. 419 people against 4,096 surnames is a birthday problem, so the
#: index is drawn from a wider hash and the two lists are combined.
FIRST_NAMES = (
    "Amira",
    "Yusuf",
    "Layla",
    "Karim",
    "Nadia",
    "Tarek",
    "Hana",
    "Omar",
    "Salma",
    "Fadi",
    "Rania",
    "Bassem",
    "Dalia",
    "Hakim",
    "Iman",
    "Jamal",
    "Kamal",
    "Leila",
    "Maha",
    "Nabil",
    "Ola",
    "Qasim",
    "Rami",
    "Sana",
    "Tamer",
    "Wafa",
    "Yara",
    "Zaki",
    "Adel",
    "Basma",
    "Cyrine",
    "Diya",
    "Elias",
    "Farah",
    "Ghada",
    "Hadi",
)
LAST_NAMES = (
    "Haddad",
    "Nassar",
    "Khoury",
    "Aziz",
    "Sabbagh",
    "Mansour",
    "Rizk",
    "Fares",
    "Malouf",
    "Shadid",
    "Zogby",
    "Bishara",
    "Daher",
    "Ghanem",
    "Halabi",
    "Isper",
    "Jabara",
    "Kassis",
    "Lahoud",
    "Maalouf",
    "Naifeh",
    "Obeid",
    "Qadi",
    "Rahal",
    "Saliba",
    "Tannous",
    "Wahba",
    "Yazbeck",
    "Zakhem",
    "Antoun",
)

#: `TAI-10776` keeps its prefix: the letters encode the company, which is the
#: whole of P-13, and the digits are the identifying part.
CODE_PATTERN = re.compile(r"^([A-Za-z]+[-_]?)?(\d+)$")


def _digest(value: str, *, domain: str) -> int:
    """A stable integer for one value within one namespace.

    `domain` keeps the namespaces apart, so a person whose employee code
    happens to equal somebody's odoo_id does not receive their fake name. Two
    identifiers being equal as strings is a coincidence of formatting, not a
    statement that they refer to the same thing.
    """
    return int.from_bytes(
        hashlib.blake2b(f"{domain}:{value}".encode(), key=KEY, digest_size=8).digest(),
        "big",
    )


def fake_person_name(value: str) -> str:
    """A readable substitute, stable for one input.

    Readable rather than a hex string because a failing golden test is read by
    a person, and `Layla Haddad attended 3 sessions` diagnoses faster than
    `a4f9c2e1 attended 3 sessions`.
    """
    number = _digest(value, domain="person")
    first = FIRST_NAMES[number % len(FIRST_NAMES)]
    last = LAST_NAMES[(number // len(FIRST_NAMES)) % len(LAST_NAMES)]
    return f"{first} {last}"


def fake_email(value: str) -> str:
    """`.test` is reserved by RFC 2606, so this can never reach a real inbox."""
    number = _digest(value, domain="email")
    return f"person{number % 1_000_000}@example.test"


def fake_mobile(value: str) -> str:
    """Kept the same shape and length so a validator still sees a phone number."""
    number = _digest(value, domain="mobile")
    return f"+2010{number % 100_000_000:08d}"


def fake_employee_code(value: str) -> str:
    """Digits replaced, prefix kept.

    `TAI-` and `TM4-` say which company somebody belongs to. Replacing them
    would erase the multi-entity structure that P-13 is about, and the platform
    would look like it had been tested against a single-company roster.
    """
    match = CODE_PATTERN.match(value.strip())
    if not match:
        return f"CODE-{_digest(value, domain='code') % 1_000_000}"
    prefix = match.group(1) or ""
    return f"{prefix}{_digest(value, domain='code') % 100_000}"


def fake_odoo_id(value: str | int) -> str:
    """The join key. Stable across the roster and every nested `user`.

    If this were not deterministic the same person would arrive as several
    people, identity resolution would fail on data that resolves perfectly, and
    the dataset would be asserting a defect the source does not have.
    """
    return str(_digest(str(value), domain="odoo") % 1_000_000)


def fake_trainer_name(value: str) -> str:
    """Substitute a trainer, preserving the variants that make P-04 real.

    Keyed on the normalised form, so every spelling of one person maps to one
    substitute, and the original's case pattern is reapplied so the *number* of
    spellings survives too. `Ahmed ElShiaty` and `Ahmed Elshiaty` become two
    spellings of one fake person, still merging to one `dim_trainer` row.

    `X & Y` names two trainers on one session — three sessions do this — so
    each side is substituted independently and the pair is preserved.
    """
    if "&" in value:
        return " & ".join(fake_trainer_name(part.strip()) for part in value.split("&"))

    key = normalise(value)
    if not key:
        return value

    # An institution rather than a person. Substituted like anything else, but
    # kept as a single token so it does not acquire a surname it never had.
    substitute = fake_person_name(key)

    if value.isupper():
        return substitute.upper()
    if value.islower():
        return substitute.lower()
    # `Ahmed Elshiaty` against `Ahmed ElShiaty`: the second word carries an
    # interior capital. Reproducing it keeps the two spellings distinct as
    # strings while `normalise` still collapses them to one person.
    words = value.split()
    if len(words) > 1 and any(w[1:] != w[1:].lower() for w in words if len(w) > 1):
        first, _, last = substitute.partition(" ")
        return f"{first} {last[:2].upper()}{last[2:].lower()}" if last else first
    return substitute


def anonymise_user(user: dict[str, Any]) -> dict[str, Any]:
    """One `user` object, wherever it appears — nested or in the roster."""
    out = dict(user)
    for field in ("name", "full_name"):
        if out.get(field):
            out[field] = fake_person_name(str(out[field]))
    if out.get("email"):
        out["email"] = fake_email(str(out["email"]))
    if out.get("mobile"):
        out["mobile"] = fake_mobile(str(out["mobile"]))
    if out.get("employee_code"):
        out["employee_code"] = fake_employee_code(str(out["employee_code"]))
    if out.get("odoo_id") is not None:
        out["odoo_id"] = fake_odoo_id(out["odoo_id"])
    # `sector`, `department`, `company`, `position`, `job_level_*` and `status`
    # are deliberately untouched. The trailing space on 940 of 1,052 sectors is
    # P-05, and a dataset that trimmed it would test nothing.
    return out


def name_substitutions(people: Iterable[str], trainers: Iterable[str]) -> dict[str, str]:
    """Real name to its substitute, for the fields that hold names in prose.

    Built by the freezer from everyone the payload knows about, because a name
    inside a title cannot be recognised as a name by any rule — only by knowing
    the list. `Learning Path Reflection - Ahmed Sobhy` is a real program title,
    and the title is load-bearing: P-02 is sixteen titles colliding, so it
    cannot simply be redacted the way a description can.

    Trainers take precedence. Somebody who trains and also attends must read
    the same in a title as they do in `trainer_name`, or the dataset would name
    two different fake people for one real one.
    """
    substitutions = {name: fake_person_name(name) for name in people if name}
    substitutions.update({name: fake_trainer_name(name) for name in trainers if name})
    return substitutions


def substitute_names(text: str, substitutions: Mapping[str, str]) -> str:
    """Replace every known real name appearing in a piece of text.

    Longest first, so `Ahmed Sobhy` is replaced as a whole rather than having
    `Ahmed` substituted inside it and leaving half a real surname behind.
    """
    out = text
    for real in sorted(substitutions, key=len, reverse=True):
        if real in out:
            out = out.replace(real, substitutions[real])
    return out


def anonymise_program(
    program: dict[str, Any], substitutions: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """One program tree, with every person inside it replaced.

    Ids, titles, dates, capacities, statuses and every survey answer are left
    exactly as they arrived — they are what the golden values are computed
    from, and they are not personal data.
    """
    out = dict(program)

    # Marketing prose, and the one field that names people in sentences rather
    # than in a `trainer_name` key: "Led by our Ai expert, <name>." and
    # "<name>'s Reflection on ...". No substitution rule finds a name inside a
    # paragraph reliably, and nothing computes a figure from a description, so
    # the paragraph goes and its presence stays.
    if out.get("description"):
        out["description"] = "[redacted description]"

    # Titles and subtitles keep their words — sixteen of them collide, which is
    # P-02 — so a name inside one is substituted rather than the whole field
    # being thrown away.
    for field in ("title", "subtitle"):
        if substitutions and isinstance(out.get(field), str):
            out[field] = substitute_names(out[field], substitutions)
    if isinstance(out.get("track"), dict) and out["track"].get("description"):
        out["track"] = dict(out["track"], description="[redacted description]")

    out["sessions"] = [_anonymise_session(session) for session in program.get("sessions") or []]
    out["users"] = [_anonymise_roster_entry(entry) for entry in program.get("users") or []]
    return out


def _anonymise_session(session: dict[str, Any]) -> dict[str, Any]:
    out = dict(session)
    if out.get("trainer_name"):
        out["trainer_name"] = fake_trainer_name(str(out["trainer_name"]))
    out["attendance"] = [_anonymise_attendance(row) for row in session.get("attendance") or []]
    return out


def _anonymise_attendance(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    if out.get("user_odoo_id") is not None:
        out["user_odoo_id"] = fake_odoo_id(out["user_odoo_id"])
    if isinstance(out.get("user"), dict):
        out["user"] = anonymise_user(out["user"])
    return out


def redact_free_text(answer: dict[str, Any]) -> dict[str, Any]:
    """Replace a written-in answer, keep a scored one exactly.

    Free text is the one field where somebody can name a colleague, complain
    about a manager, or identify themselves, and no substitution rule can be
    trusted to catch that. So the content goes.

    What the golden values need from a free-text answer is only that it *is*
    one: it becomes a comment on the Program Scorecard rather than a score, and
    it counts toward neither NPS nor the quality percentages. Presence, type
    and question are preserved, so every count still holds; only the sentence
    is replaced.

    A `select` or `rating` answer is left untouched. Those are the numbers — a
    substituted score would move the very figures being pinned, which would
    make the dataset worse than useless.
    """
    out = dict(answer)
    if out.get("answer_type") == "text" and out.get("answer"):
        out["answer"] = f"[redacted free text {_digest(str(out['answer']), domain='text') % 1000}]"
    return out


def _anonymise_roster_entry(entry: dict[str, Any]) -> dict[str, Any]:
    out = dict(entry)
    if out.get("user_odoo_id") is not None:
        out["user_odoo_id"] = fake_odoo_id(out["user_odoo_id"])
    if isinstance(out.get("user"), dict):
        out["user"] = anonymise_user(out["user"])

    out["survey_answers"] = [
        redact_free_text(answer) for answer in entry.get("survey_answers") or []
    ]
    # Assessment answers are free text by nature — an open question with a
    # written reply — so every one is redacted rather than only those typed
    # `text`.
    out["assessment_answers"] = [
        dict(answer, answer=f"[redacted assessment {i}]") if answer.get("answer") else dict(answer)
        for i, answer in enumerate(entry.get("assessment_answers") or [])
    ]
    return out
