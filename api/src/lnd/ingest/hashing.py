"""Payload fingerprinting.

Every record landed in `raw` carries a hash of its own content. That single
value does three jobs:

    dedup       re-fetching an unchanged record is a no-op
    change      a differing hash is the only reliable signal that a source
    detection   edited something, since not every system updates `updated_at`
    integrity   a stored payload can be re-verified against its own hash

The hash must be stable across processes and Python versions, so the payload is
serialised canonically first: keys sorted, no incidental whitespace, UTF-8. Two
dicts that differ only in key order hash identically, because they carry
identical information.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Prefix so a stored hash is self-describing and a future change of algorithm is
# distinguishable rather than silently incompatible.
_ALGORITHM = "sha256"


def canonical_json(payload: Any) -> str:
    """Serialise deterministically: sorted keys, compact, UTF-8 preserved."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def payload_hash(payload: Any) -> str:
    """Return `sha256:<hex>` for a source payload."""
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"{_ALGORITHM}:{digest}"


def matches(payload: Any, expected_hash: str) -> bool:
    """True if `payload` still hashes to `expected_hash`."""
    return payload_hash(payload) == expected_hash
