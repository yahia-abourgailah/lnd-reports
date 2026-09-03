"""Contract fixtures — record and replay.

Week 1 asks for every CRM response to be saved. The value is that a source
schema change breaks CI with a diff, rather than breaking the dashboard with a
null. Replay must therefore be exact: a fixture that had been tidied on the way
in or out would test nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lnd.sources import fixtures

PAYLOADS = [
    {"crm_program_id": "87", "title": "Hard Talks", "capacity": 20},
    {"crm_program_id": "81", "title": "Hard Talks", "capacity": 15},
]


def test_a_recorded_response_replays_identically(tmp_path: Path) -> None:
    fixtures.record("crm", "program", PAYLOADS, base_dir=tmp_path)
    assert fixtures.replay("crm", "program", base_dir=tmp_path) == PAYLOADS


def test_replay_touches_no_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point: contract tests run with every source switched off."""
    import httpx

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("replay attempted a network call")

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    fixtures.record("crm", "program", PAYLOADS, base_dir=tmp_path)
    assert len(fixtures.replay("crm", "program", base_dir=tmp_path)) == 2


def test_unicode_survives_a_round_trip(tmp_path: Path) -> None:
    payloads = [{"trainer": "أحمد الشياتي", "title": "Hard Talks"}]
    fixtures.record("crm", "program", payloads, base_dir=tmp_path)
    assert fixtures.replay("crm", "program", base_dir=tmp_path) == payloads


def test_whitespace_is_preserved_exactly(tmp_path: Path) -> None:
    """P-05 lives in trailing spaces. A fixture that trimmed them would hide
    the very defect the contract test exists to catch."""
    payloads = [{"sector": "Projects "}]
    fixtures.record("crm", "program", payloads, base_dir=tmp_path)
    assert fixtures.replay("crm", "program", base_dir=tmp_path)[0]["sector"] == "Projects "


def test_a_missing_fixture_says_how_to_create_one(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="SOURCE_RECORD_FIXTURES=true"):
        fixtures.replay("crm", "program", base_dir=tmp_path)


def test_a_malformed_fixture_is_refused(tmp_path: Path) -> None:
    path = fixtures.fixture_path("crm", "program", base_dir=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"not": "a list"}', "utf-8")

    with pytest.raises(ValueError, match="should hold a list"):
        fixtures.replay("crm", "program", base_dir=tmp_path)


def test_recording_replaces_the_previous_recording(tmp_path: Path) -> None:
    fixtures.record("crm", "program", PAYLOADS, base_dir=tmp_path)
    fixtures.record("crm", "program", [PAYLOADS[0]], base_dir=tmp_path)
    assert len(fixtures.replay("crm", "program", base_dir=tmp_path)) == 1


def test_sources_and_entities_get_separate_files(tmp_path: Path) -> None:
    fixtures.record("crm", "program", PAYLOADS, base_dir=tmp_path)
    fixtures.record("crm", "session", [{"crm_session_id": "1041"}], base_dir=tmp_path)
    fixtures.record("hris", "employee", [{"employee_code": "E1"}], base_dir=tmp_path)

    assert fixtures.available(base_dir=tmp_path) == [
        ("crm", "program"),
        ("crm", "session"),
        ("hris", "employee"),
    ]


def test_available_is_empty_before_anything_is_recorded(tmp_path: Path) -> None:
    assert fixtures.available(base_dir=tmp_path / "nothing-here") == []


def test_recording_is_refused_in_production() -> None:
    """Source payloads contain PII. A recording made against production would
    write real employee records to disk inside a repository."""
    from lnd.config import Settings

    with pytest.raises(ValueError, match="never be enabled in production"):
        Settings(
            environment="production",  # type: ignore[arg-type]
            session_secret="a" * 40,
            oidc_discovery_url="https://idp.example.com/.well-known/openid-configuration",
            oidc_client_id="client",
            oidc_client_secret="secret",
            oidc_redirect_uri="https://lnd.example.com/v1/auth/callback",
            source_record_fixtures=True,
        )
