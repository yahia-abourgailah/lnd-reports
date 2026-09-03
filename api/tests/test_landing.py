"""The raw landing layer, against a real PostgreSQL.

Three properties are asserted here, and they are the reason the raw layer exists
at all:

    idempotency  a repeated sync writes nothing new
    history      a source edit appends, never overwrites
    replay       the latest state is reconstructable with no source call

Everything downstream depends on these. If landing were not idempotent, a Celery
retry would double an attendance count and no test elsewhere would notice.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from lnd.ingest import Entity, RawRecord, Source, current, history, land
from lnd.ingest.hashing import payload_hash

PROGRAM_87 = {"crm_program_id": "87", "title": "Hard Talks", "capacity": 20}
PROGRAM_81 = {"crm_program_id": "81", "title": "Hard Talks", "capacity": 15}


def _land(db: Session, records: list[tuple[str, dict[str, object]]], **kwargs: object):  # type: ignore[no-untyped-def]
    return land(db, source=Source.CRM, entity=Entity.PROGRAM, records=records, **kwargs)  # type: ignore[arg-type]


# ------------------------------------------------------------------ the basics
def test_a_record_lands_exactly_as_it_arrived(db: Session) -> None:
    result = _land(db, [("87", PROGRAM_87)])

    assert result.received == 1
    assert result.landed == 1

    stored = db.query(RawRecord).one()
    assert stored.payload == PROGRAM_87
    assert stored.source == "crm"
    assert stored.entity == "program"
    assert stored.source_id == "87"
    assert stored.payload_hash == payload_hash(PROGRAM_87)


def test_landing_nothing_is_not_an_error(db: Session) -> None:
    """An empty sync window is normal, not a failure."""
    result = _land(db, [])
    assert (result.received, result.landed, result.unchanged) == (0, 0, 0)


def test_every_received_record_is_accounted_for(db: Session) -> None:
    """The raw-layer counterpart of the transform invariant: received equals
    landed plus unchanged, always. LandingResult asserts it on construction."""
    _land(db, [("87", PROGRAM_87)])
    result = _land(db, [("87", PROGRAM_87), ("81", PROGRAM_81)])
    assert result.received == result.landed + result.unchanged == 2


# ----------------------------------------------------------------- idempotency
def test_landing_the_same_payload_twice_writes_one_row(db: Session) -> None:
    """FR-A10. Celery retries tasks on worker loss; a retry that duplicated
    attendance would drift every downstream number silently."""
    first = _land(db, [("87", PROGRAM_87)])
    second = _land(db, [("87", PROGRAM_87)])

    assert first.landed == 1
    assert second.landed == 0
    assert second.unchanged == 1
    assert db.query(RawRecord).count() == 1


def test_replaying_a_whole_window_is_a_no_op(db: Session) -> None:
    batch = [("87", PROGRAM_87), ("81", PROGRAM_81)]
    _land(db, batch)
    again = _land(db, batch)

    assert again.landed == 0
    assert db.query(RawRecord).count() == 2


def test_a_duplicate_within_one_page_collapses(db: Session) -> None:
    """A source can repeat a record inside a single response — duplicate scans
    do exactly this. The insert must not conflict with its own rows."""
    result = _land(db, [("87", PROGRAM_87), ("87", PROGRAM_87)])
    assert result.landed == 1
    assert db.query(RawRecord).count() == 1


# --------------------------------------------------------------------- history
def test_an_edited_record_appends_rather_than_overwrites(db: Session) -> None:
    """The CRM changing capacity from 20 to 25 must leave both facts on record."""
    _land(db, [("87", PROGRAM_87)])
    _land(db, [("87", {**PROGRAM_87, "capacity": 25})])

    versions = history(db, source=Source.CRM, entity=Entity.PROGRAM, source_id="87")
    assert len(versions) == 2
    assert versions[0].payload["capacity"] == 20
    assert versions[1].payload["capacity"] == 25


def test_history_is_oldest_first(db: Session) -> None:
    for capacity in (20, 25, 30):
        _land(db, [("87", {**PROGRAM_87, "capacity": capacity})])

    versions = history(db, source=Source.CRM, entity=Entity.PROGRAM, source_id="87")
    assert [v.payload["capacity"] for v in versions] == [20, 25, 30]


def test_a_source_edit_is_detected_even_without_an_updated_at(db: Session) -> None:
    """Not every system bumps `updated_at`. The hash is what actually decides."""
    _land(db, [("87", PROGRAM_87)])
    result = _land(db, [("87", {**PROGRAM_87, "title": "Hard Talks (rerun)"})])
    assert result.landed == 1


# ---------------------------------------------------------------------- replay
def test_current_returns_the_latest_version_of_each_record(db: Session) -> None:
    """This is the replay path the transform reads. No source is contacted."""
    _land(db, [("87", PROGRAM_87), ("81", PROGRAM_81)])
    _land(db, [("87", {**PROGRAM_87, "capacity": 25})])

    latest = current(db, source=Source.CRM, entity=Entity.PROGRAM)
    by_id = {record.source_id: record for record in latest}

    assert len(latest) == 2
    assert by_id["87"].payload["capacity"] == 25
    assert by_id["81"].payload["capacity"] == 15


def test_current_can_be_narrowed_to_specific_records(db: Session) -> None:
    _land(db, [("87", PROGRAM_87), ("81", PROGRAM_81)])
    narrowed = current(db, source=Source.CRM, entity=Entity.PROGRAM, source_ids=["81"])
    assert [r.source_id for r in narrowed] == ["81"]


def test_current_is_empty_before_anything_lands(db: Session) -> None:
    assert current(db, source=Source.CRM, entity=Entity.PROGRAM) == []


# ----------------------------------------------------------------- separation
def test_sources_and_entities_do_not_collide(db: Session) -> None:
    """A CRM program id and an HRIS employee id may both be "87". They are
    different records and must never be confused."""
    land(db, source=Source.CRM, entity=Entity.PROGRAM, records=[("87", {"a": 1})])
    land(db, source=Source.HRIS, entity=Entity.EMPLOYEE, records=[("87", {"a": 1})])
    land(db, source=Source.CRM, entity=Entity.SESSION, records=[("87", {"a": 1})])

    assert db.query(RawRecord).count() == 3
    assert len(current(db, source=Source.CRM, entity=Entity.PROGRAM)) == 1


# --------------------------------------------------------------------- audit
def test_the_sync_run_is_recorded_on_each_row(db: Session) -> None:
    """Week 2's audit trail: which sync produced which row."""
    # ops.sync_run.id is a BigInteger, not the UUID this column first assumed
    # (see migration 0005). The audit link is only useful if the two agree.
    run_id = 4271
    _land(db, [("87", PROGRAM_87)], sync_run_id=run_id)
    assert db.query(RawRecord).one().sync_run_id == run_id


# ------------------------------------------------------- immutability in the db
def test_the_application_role_cannot_rewrite_raw(db: Session) -> None:
    """Migration 0001 grants the application role SELECT and INSERT on raw and
    nothing else. This asserts the grant itself, not merely our intent."""
    from sqlalchemy import text

    can_insert, can_update, can_delete = db.execute(
        text(
            "SELECT has_table_privilege('lnd_app', 'raw.source_record', 'INSERT'), "
            "       has_table_privilege('lnd_app', 'raw.source_record', 'UPDATE'), "
            "       has_table_privilege('lnd_app', 'raw.source_record', 'DELETE')"
        )
    ).one()

    assert can_insert is True, "the pipeline must be able to append"
    assert can_update is False, "raw must not be rewritable"
    assert can_delete is False, "raw must not be erasable"


@pytest.mark.parametrize("entity", list(Entity))
def test_every_entity_can_land(db: Session, entity: Entity) -> None:
    """All seven entity kinds across the four sources use one table."""
    result = land(db, source=Source.CRM, entity=entity, records=[("1", {"x": 1})])
    assert result.landed == 1


# ------------------------------------------------------- CRM → raw, end to end
def test_a_fetched_program_lands_and_replays(db: Session) -> None:
    """The whole week-1 path: stubbed CRM → client → raw → read back.

    Nothing between the response and the stored row alters the payload, so what
    replays in week 4 is byte-for-byte what the CRM said.
    """
    import httpx

    from lnd.sources.crm import CrmClient, parse_programs, program_id_of
    from tests.fixtures import crm_program

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=crm_program.page())

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://crm.example.test")
    with CrmClient(base_url="https://crm.example.test", service_key="k", client=http) as crm:
        payloads = crm.fetch_programs()

    result = land(
        db,
        source=Source.CRM,
        entity=Entity.PROGRAM,
        records=[(program_id_of(p), p) for p in payloads],
    )
    assert result.landed == 1

    replayed = current(db, source=Source.CRM, entity=Entity.PROGRAM)
    assert replayed[0].payload == payloads[0]

    # And the stored tree still validates — sessions, roster and answers intact.
    parsed, rejected = parse_programs([r.payload for r in replayed])
    assert rejected == []
    assert parsed[0].id == 718
    assert len(parsed[0].sessions) == 2
    assert len(parsed[0].users) == 2
    assert parsed[0].users[0].survey_answers[0].answer == "Very useful"


def test_re_fetching_an_unchanged_program_lands_nothing(db: Session) -> None:
    """A 30-minute sync over 55 unchanged programs must write zero rows."""
    from lnd.sources.crm import program_id_of
    from tests.fixtures import crm_program

    payload = crm_program.program()
    records = [(program_id_of(payload), payload)]

    assert land(db, source=Source.CRM, entity=Entity.PROGRAM, records=records).landed == 1
    assert land(db, source=Source.CRM, entity=Entity.PROGRAM, records=records).landed == 0


def test_one_new_attendance_row_lands_a_new_version(db: Session) -> None:
    """Attendance is nested inside the program tree, so a single new scan
    changes the program's hash. That is correct: the response really did
    change, and both versions stay on record."""
    from lnd.sources.crm import program_id_of
    from tests.fixtures import crm_program

    before = crm_program.program()
    land(db, source=Source.CRM, entity=Entity.PROGRAM, records=[(program_id_of(before), before)])

    after = crm_program.program()
    after["sessions"][1]["attendance"] = [
        {
            "id": 1168,
            "user_odoo_id": "4977",
            "user": None,
            "attended_at": "2026-09-11T10:00:00+03:00",
        }
    ]
    result = land(
        db, source=Source.CRM, entity=Entity.PROGRAM, records=[(program_id_of(after), after)]
    )

    assert result.landed == 1
    versions = history(db, source=Source.CRM, entity=Entity.PROGRAM, source_id="718")
    assert len(versions) == 2
    assert versions[0].payload["sessions"][1]["attendance"] == []
    assert len(versions[1].payload["sessions"][1]["attendance"]) == 1
