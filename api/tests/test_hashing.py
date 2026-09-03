"""Payload fingerprinting.

The hash decides whether a record is new, changed, or already stored. If it
were unstable, every sync would look like a change and the raw table would grow
without bound; if it were too loose, a real edit would be missed and the
dashboard would keep serving a stale figure.
"""

from __future__ import annotations

from lnd.ingest.hashing import canonical_json, matches, payload_hash


def test_the_same_payload_always_hashes_the_same() -> None:
    payload = {"crm_program_id": "87", "title": "Hard Talks"}
    assert payload_hash(payload) == payload_hash(payload)


def test_key_order_does_not_change_the_hash() -> None:
    """Two dicts differing only in key order carry identical information.

    JSON APIs make no ordering guarantee, so without this every sync would
    report spurious changes.
    """
    a = {"crm_program_id": "87", "title": "Hard Talks", "capacity": 20}
    b = {"capacity": 20, "title": "Hard Talks", "crm_program_id": "87"}
    assert payload_hash(a) == payload_hash(b)


def test_a_changed_value_changes_the_hash() -> None:
    before = {"crm_program_id": "87", "capacity": 20}
    after = {"crm_program_id": "87", "capacity": 25}
    assert payload_hash(before) != payload_hash(after)


def test_an_added_field_changes_the_hash() -> None:
    assert payload_hash({"id": "87"}) != payload_hash({"id": "87", "track": "Leadership"})


def test_trailing_whitespace_is_a_difference() -> None:
    """P-05 is a real defect in the workbook: `Projects` and `Projects ` were
    two pivot rows. The raw layer must preserve that distinction so the
    transform can see it and normalise deliberately, rather than the hash
    hiding it here."""
    assert payload_hash({"sector": "Projects"}) != payload_hash({"sector": "Projects "})


def test_null_and_missing_are_different() -> None:
    """`capacity: null` means the CRM answered "no capacity"; an absent key
    means it did not answer. Those are different facts."""
    assert payload_hash({"id": "1", "capacity": None}) != payload_hash({"id": "1"})


def test_nested_structures_hash_stably() -> None:
    a = {"id": "1", "sessions": [{"n": 1, "d": "2026-02-01"}, {"n": 2, "d": "2026-02-02"}]}
    b = {"sessions": [{"d": "2026-02-01", "n": 1}, {"d": "2026-02-02", "n": 2}], "id": "1"}
    assert payload_hash(a) == payload_hash(b)


def test_list_order_is_significant() -> None:
    """Reordering a list is a genuine change — the source chose that order."""
    a = {"sessions": [1, 2]}
    b = {"sessions": [2, 1]}
    assert payload_hash(a) != payload_hash(b)


def test_the_hash_names_its_algorithm() -> None:
    """Self-describing, so a future change of algorithm is visible rather than
    silently incompatible with stored values."""
    digest = payload_hash({"id": "1"})
    algorithm, _, hexdigest = digest.partition(":")
    assert algorithm == "sha256"
    assert len(hexdigest) == 64


def test_unicode_survives_serialisation() -> None:
    """Trainer and program names are not all ASCII."""
    payload = {"trainer": "أحمد الشياتي"}
    assert "أحمد" in canonical_json(payload)
    assert payload_hash(payload) == payload_hash({"trainer": "أحمد الشياتي"})


def test_matches_verifies_a_stored_payload() -> None:
    payload = {"id": "87", "title": "Hard Talks"}
    stored = payload_hash(payload)
    assert matches(payload, stored)
    assert not matches({"id": "87", "title": "Hard Talks II"}, stored)
