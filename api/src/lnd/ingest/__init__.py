"""The raw landing layer.

Source payloads are stored exactly as received, before any cleaning, and are
never mutated afterwards. Everything downstream is derived from this table, so
`core` is a pure function of (raw + enrichment) and any transform fix can be
replayed over history without re-querying a source.
"""

from lnd.ingest.hashing import canonical_json, matches, payload_hash
from lnd.ingest.landing import LandingResult, current, history, land
from lnd.ingest.models import Entity, RawRecord, Source

__all__ = [
    "Entity",
    "LandingResult",
    "RawRecord",
    "Source",
    "canonical_json",
    "current",
    "history",
    "land",
    "matches",
    "payload_hash",
]
