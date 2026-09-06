"""Build the frozen reference dataset from a live `raw` layer.

    python -m lnd.reference.freeze            # writes tests/reference/dataset.json.gz
    python -m lnd.reference.freeze --check    # regenerates and diffs, changing nothing

Reads `raw.source_record`, anonymises, and writes one gzipped JSON file. Reads
`raw` rather than `core` on purpose: the dataset has to enter the pipeline where
the CRM's own payloads do, so that freezing it exercises validation, the shred
into three grains, identity resolution and the exception rules. A snapshot of
`core` would pin the output of the transform against itself and notice nothing.

WHEN TO REGENERATE

Rarely, and never casually. This file is the definition of "the numbers did not
move", so replacing it replaces the thing the golden suite is measuring
against. Regenerate when the CRM's payload *shape* changes — a new field, a
renamed key, an endpoint that starts returning something different — and say so
in the pull request. Do not regenerate to make a failing test pass; a failing
golden test is the system working.

`--check` exists for CI: it proves the committed dataset is what this code
produces from the same input, so a hand-edited fixture cannot drift into the
repository unnoticed.
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from lnd.db import session_scope
from lnd.ingest.landing import current
from lnd.ingest.models import Entity, Source
from lnd.reference.anonymise import (
    KEY,
    anonymise_program,
    anonymise_user,
    name_substitutions,
)

log = logging.getLogger(__name__)

#: Beside the tests that read it, not in `docs/`. It is an input to the suite,
#: and a fixture that lives away from its tests is a fixture that rots.
DATASET = Path(__file__).resolve().parents[3] / "tests" / "reference" / "dataset.json.gz"

#: Gzip because 1.1 MB of JSON in every clone, diff and CI checkout is a cost
#: paid forever for something no one reads by eye.
COMPRESS_LEVEL = 9


class PiiLeak(RuntimeError):
    """A real person survived anonymisation. Nothing is written."""


def _person_values(payload: Any, found: set[str]) -> None:
    """Every value in a payload that names or contacts a real person.

    Walks for `user`-shaped objects — anything carrying an `odoo_id` beside a
    name or an email — plus `trainer_name` wherever it appears. Department,
    company and position names are deliberately not collected: they are kept in
    the dataset on purpose and are not personal data.
    """
    if isinstance(payload, dict):
        if "odoo_id" in payload and ("full_name" in payload or "email" in payload):
            for field in ("name", "full_name", "email", "mobile"):
                value = payload.get(field)
                if isinstance(value, str) and value.strip():
                    found.add(value.strip())
        trainer = payload.get("trainer_name")
        if isinstance(trainer, str) and trainer.strip():
            found.add(trainer.strip())
        for value in payload.values():
            _person_values(value, found)
    elif isinstance(payload, list):
        for item in payload:
            _person_values(item, found)


def _collect_names(payload: Any, people: set[str], trainers: set[str]) -> None:
    """Split the person values into the two groups that substitute differently.

    A trainer is substituted by `fake_trainer_name`, which preserves spelling
    variants; anybody else by `fake_person_name`. Keeping them apart here is
    what stops one real person acquiring two fake identities depending on which
    field mentioned them.
    """
    if isinstance(payload, dict):
        if "odoo_id" in payload and ("full_name" in payload or "email" in payload):
            for field in ("name", "full_name"):
                value = payload.get(field)
                if isinstance(value, str) and value.strip():
                    people.add(value.strip())
        trainer = payload.get("trainer_name")
        if isinstance(trainer, str) and trainer.strip():
            trainers.add(trainer.strip())
        for value in payload.values():
            _collect_names(value, people, trainers)
    elif isinstance(payload, list):
        for item in payload:
            _collect_names(item, people, trainers)


def _assert_no_leak(source: list[dict[str, Any]], anonymised: dict[str, Any]) -> None:
    """Refuse to write a dataset that still contains a real person.

    The check belongs here rather than in a test, because this is the only
    moment both halves exist: the test suite has the frozen file and no real
    payload to compare it against, and by the time a leak reaches a test it is
    already committed and already in everyone's clone.

    It is a substring search over the serialised output, so it catches a name
    that survived in a field nobody thought to anonymise — which is exactly how
    the first version leaked two trainers inside program descriptions, in
    prose, while every `trainer_name` was substituted correctly.
    """
    real: set[str] = set()
    for payload in source:
        _person_values(payload, real)

    blob = _serialise(anonymised).decode()
    # Short values collide with ids and dates by coincidence; a person's name
    # or email is never three characters.
    leaked = sorted(value for value in real if len(value) > 3 and value in blob)
    if leaked:
        raise PiiLeak(
            f"{len(leaked)} real value(s) survived anonymisation, nothing written: "
            + ", ".join(repr(value) for value in leaked[:5])
        )


def build(session: Session) -> dict[str, Any]:
    """Read `raw`, anonymise, and return the dataset as a plain dict."""
    raw_programs = [
        record.payload for record in current(session, source=Source.CRM, entity=Entity.PROGRAM)
    ]
    raw_roster = [
        record.payload for record in current(session, source=Source.CRM, entity=Entity.EMPLOYEE)
    ]

    # Names have to be collected before anything is substituted, because a name
    # appearing inside a *title* is only recognisable as a name by knowing the
    # list of them.
    people: set[str] = set()
    trainers: set[str] = set()
    for payload in raw_programs + raw_roster:
        _collect_names(payload, people, trainers)
    substitutions = name_substitutions(people, trainers)

    programs = [anonymise_program(payload, substitutions) for payload in raw_programs]
    roster = [anonymise_user(payload) for payload in raw_roster]

    # Sorted by the source's own id so the file has a stable order. Without
    # this, `current()` returning rows in a different order would produce a
    # diff on every regeneration and hide the change that actually mattered.
    programs.sort(key=lambda program: program.get("id") or 0)
    roster.sort(key=lambda user: str(user.get("odoo_id") or ""))

    dataset = {
        "meta": {
            "note": (
                "Frozen reference dataset. Real CRM structure, substituted "
                "identities. Regenerate only when the payload shape changes — "
                "see lnd.reference.freeze."
            ),
            "anonymisation_key": KEY.decode(),
            "frozen_at": datetime.now(UTC).date().isoformat(),
            "programs": len(programs),
            "roster": len(roster),
        },
        "programs": programs,
        "roster": roster,
    }

    _assert_no_leak(raw_programs + raw_roster, dataset)
    return dataset


def load(path: Path = DATASET) -> dict[str, Any]:
    """Read the committed dataset. What the golden suite calls."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        loaded: dict[str, Any] = json.load(handle)
        return loaded


def _serialise(dataset: dict[str, Any]) -> bytes:
    """Deterministic bytes: sorted keys, fixed separators, trailing newline."""
    return (
        json.dumps(dataset, sort_keys=True, indent=1, ensure_ascii=False, default=str) + "\n"
    ).encode()


def write(dataset: dict[str, Any], path: Path = DATASET) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # `mtime=0` so the bytes depend only on the content. Gzip stamps the
    # current time into its header by default, which would make every
    # regeneration produce a different file for identical data — a diff on
    # every run, and nothing anyone would keep reading.
    with gzip.GzipFile(
        filename=str(path), mode="wb", compresslevel=COMPRESS_LEVEL, mtime=0
    ) as handle:
        handle.write(_serialise(dataset))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate in memory and compare, writing nothing",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    with session_scope() as session:
        dataset = build(session)

    if not dataset["programs"]:
        log.error("raw holds no programs; sync before freezing")
        return 2

    if args.check:
        # `meta.frozen_at` is a date, so it differs whenever the check runs on
        # another day. The comparison is over the data, which is what the
        # golden values are computed from and the only part that may not drift.
        committed = load()
        same = committed["programs"] == dataset["programs"] and (
            committed["roster"] == dataset["roster"]
        )
        log.info("reference dataset %s", "matches" if same else "DIFFERS from raw")
        return 0 if same else 1

    write(dataset)
    log.info(
        "wrote %s — %d programs, %d roster rows, %.0f kB",
        DATASET.name,
        len(dataset["programs"]),
        len(dataset["roster"]),
        DATASET.stat().st_size / 1024,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - a script
    raise SystemExit(main())
