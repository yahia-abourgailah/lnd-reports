"""Contract fixtures.

Week 1 asks for every CRM response to be saved as a fixture. The point is not
convenience — it is that a source schema change should break the *build*, not
the dashboard. A fixture is a recording of what the CRM actually said on the
day we connected; the contract test replays it, so if the CRM renames a field
next March, CI fails with a clear diff instead of a metric quietly going null.

Two modes:

    record   with a live connection, save each response verbatim
    replay   with no connection at all, read the saved responses back

Recording is switched on by `SOURCE_RECORD_FIXTURES`, never by default: an
accidental recording against production would write real employee payloads to
disk in a repository.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "contracts"


def fixture_path(source: str, entity: str, *, base_dir: Path | None = None) -> Path:
    return (base_dir or DEFAULT_FIXTURE_DIR) / source / f"{entity}.json"


def record(
    source: str, entity: str, payloads: list[dict[str, Any]], *, base_dir: Path | None = None
) -> Path:
    """Save a source response verbatim.

    Called only when recording is enabled. The payloads are written exactly as
    received — a fixture that had been tidied would not test anything.
    """
    path = fixture_path(source, entity, base_dir=base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payloads, indent=2, ensure_ascii=False, default=str), "utf-8")
    log.info(
        "recorded contract fixture",
        extra={
            "event": "fixture.recorded",
            "source": source,
            "entity": entity,
            "records": len(payloads),
            "path": str(path),
        },
    )
    return path


def replay(source: str, entity: str, *, base_dir: Path | None = None) -> list[dict[str, Any]]:
    """Read a saved response back. Never touches the network."""
    path = fixture_path(source, entity, base_dir=base_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"No contract fixture for {source}/{entity} at {path}. "
            "Record one against the real source with SOURCE_RECORD_FIXTURES=true."
        )
    data = json.loads(path.read_text("utf-8"))
    if not isinstance(data, list):
        raise ValueError(
            f"Fixture {path} should hold a list of payloads, found {type(data).__name__}."
        )
    return data


def available(*, base_dir: Path | None = None) -> list[tuple[str, str]]:
    """Every (source, entity) pair that has a recorded fixture."""
    root = base_dir or DEFAULT_FIXTURE_DIR
    if not root.exists():
        return []
    return sorted((path.parent.name, path.stem) for path in root.glob("*/*.json"))
