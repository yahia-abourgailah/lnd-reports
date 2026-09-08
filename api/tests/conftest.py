from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, create_engine, delete, func, insert, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from lnd import db as lnd_db
from lnd.config import get_settings
from lnd.models import (
    AlertNotification,
    ExportEdition,
    RawRecord,
    SavedView,
    SourcePresence,
    SyncRun,
)

# Captured at import, before the autouse fixture below replaces DATABASE_URL
# with a deliberately unreachable one. Tests that need a real database read
# this instead; without it they skip rather than fail.
#   make test  supplies it from .env; CI supplies its service container.
REAL_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

BASE_ENV = {
    "ENVIRONMENT": "dev",
    "SESSION_SECRET": "test-secret-that-is-long-enough-for-the-guard-check",
    "DATABASE_URL": "postgresql+psycopg://test:test@localhost:5432/test",
    "REDIS_URL": "redis://localhost:6379/0",
    "API_BASE_URL": "http://testserver",
    "WEB_BASE_URL": "http://testserver",
    "OIDC_REDIRECT_URI": "http://testserver/v1/auth/callback",
    "AUTH_DEV_BYPASS": "false",
}


def _reset_caches() -> None:
    from lnd import db as lnd_db
    from lnd.auth import oidc, session
    from lnd.config import get_settings

    get_settings.cache_clear()
    oidc.reset_caches()
    session._dev_secret = None
    lnd_db.dispose_engine()


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every test starts from a known, dev-shaped environment."""
    for key in list(os.environ):
        if key.split("_")[0] in {"ENVIRONMENT", "SESSION", "OIDC", "AUTH", "DATABASE", "REDIS"}:
            monkeypatch.delenv(key, raising=False)
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)

    _reset_caches()
    yield
    _reset_caches()


@pytest.fixture
def client() -> Iterator[TestClient]:
    from lnd.main import create_app

    with TestClient(create_app(), follow_redirects=False) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    """One engine for the whole session, and one decision about reachability.

    Constraints are the point of several tables in this codebase — raw's missing
    UPDATE grant, sync_run's one-active-run index, and from week 3 the transform
    invariant. None of them can be demonstrated against anything but PostgreSQL,
    so those tests need a live database rather than SQLite.

    Set `TEST_DATABASE_URL` to run them; the dev overlay publishes Postgres on
    127.0.0.1:5432 for exactly this. They skip rather than fail when it is
    absent, so `pytest` still works on a machine with nothing running — CI is
    what guarantees they actually execute.

    POINT IT AT ITS OWN DATABASE, NOT AT `lnd`

        createdb lnd_test && alembic upgrade head   (once)
        TEST_DATABASE_URL=...:5432/lnd_test pytest

    Most fixtures here truncate and roll back, so sharing the dev database
    looks safe and mostly is. What is not safe is sharing it with a *running
    stack*: beat evaluates alerts every fifteen minutes and the sync writes
    `sync_run` rows, and both are inputs the alert rules read. A test that
    pins `now` to a fixed date then disagrees with rows written by the real
    clock — six of the alert tests fail this way, for reasons that have
    nothing to do with the code under test. CI builds a fresh database per
    run, which is why it never sees this.

    Session-scoped so an unreachable database costs one connect timeout for the
    run rather than one per test, and `connect_timeout` is set because psycopg
    otherwise waits indefinitely on a port nothing is listening on — which is
    what a stack started without the dev overlay looks like.
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set; skipping database integration tests")

    engine = create_engine(url, connect_args={"connect_timeout": 5})
    try:
        with engine.connect():
            pass
    except OperationalError as exc:  # pragma: no cover - depends on the machine
        engine.dispose()
        pytest.skip(f"database at TEST_DATABASE_URL is not reachable: {exc.__class__.__name__}")

    yield engine
    engine.dispose()


@pytest.fixture
def db_connection(db_engine: Engine) -> Iterator[Connection]:
    """A connection inside a transaction that is always rolled back.

    Nothing a test writes survives it, so pointing TEST_DATABASE_URL at the dev
    database leaves it exactly as it was found.
    """
    connection = db_engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


@pytest.fixture
def live_db(db_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the application's own session factory at the test database.

    For code that commits on purpose — `record_sync_run` has to, because the
    audit of a failure must outlive the transaction that failed — and for
    anything going through the API, where the request opens its own session.
    Neither can use the rolled-back `db_connection`.

    Notes the highest existing id on the way in and deletes everything above it
    on the way out, so a dev database keeps whatever history it already had.

    UNRESOLVED ALERTS ARE THE EXCEPTION, AND HAVE TO BE PUT ASIDE

    High-water marking assumes a pre-existing row is inert — true of a
    `sync_run` and a `raw.source_record`, and false of a *live*
    `alert_notification`. `dispatch_alerts` resolves every unresolved row it
    does not re-detect, so a dev stack's own alerts get swept into whatever the
    test dispatches, stamped with the test's frozen `now`. When that `now` is
    earlier than the row's real `first_seen_at` — which it is, because the
    tests pin a date rather than read the clock — the resolution violates
    `ck_alert_notification_resolved_after_first_seen` and the test fails for a
    reason that has nothing to do with the code under test.

    It only bites when the suite shares a database with a running stack: the
    beat container evaluates alerts every fifteen minutes and opens a
    `never_synced` row per entity. CI builds a fresh database and never sees
    it, which is exactly why it is worth handling here rather than leaving as a
    trap for whoever next runs the tests against their dev database.

    So live rows are lifted out for the duration and put back afterwards, ids
    and timestamps intact.
    """
    monkeypatch.setenv("DATABASE_URL", db_engine.url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    lnd_db.dispose_engine()

    with lnd_db.session_scope() as session:
        marks = {
            model: session.scalar(select(func.coalesce(func.max(model.id), 0))) or 0
            # `ExportEdition` and `SavedView` are here for the same reason as
            # the rest: a route that writes one commits it, so it outlives the
            # test and would otherwise accumulate in whatever database the
            # suite was pointed at — including somebody's dev stack.
            for model in (
                SyncRun,
                AlertNotification,
                RawRecord,
                SourcePresence,
                ExportEdition,
                SavedView,
            )
        }
        # Captured as plain dicts rather than ORM objects: these have to
        # survive the session that read them and be reinserted into another.
        parked = [
            {
                column.name: getattr(row, column.name)
                for column in AlertNotification.__table__.columns
            }
            for row in session.scalars(
                select(AlertNotification).where(AlertNotification.resolved_at.is_(None))
            ).all()
        ]
        if parked:
            session.execute(
                delete(AlertNotification).where(
                    AlertNotification.id.in_([row["id"] for row in parked])
                )
            )

    yield

    with lnd_db.session_scope() as session:
        for model, high_water in marks.items():
            session.execute(delete(model).where(model.id > high_water))
        if parked:
            session.execute(insert(AlertNotification.__table__), parked)
    lnd_db.dispose_engine()
    get_settings.cache_clear()


@pytest.fixture
def dev_bypass_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A client whose API has the development sign-in shortcut enabled."""
    monkeypatch.setenv("AUTH_DEV_BYPASS", "true")
    monkeypatch.setenv("AUTH_DEV_USER_EMAIL", "specialist@example.com")
    monkeypatch.setenv("AUTH_DEV_USER_NAME", "L&D Specialist")
    _reset_caches()

    from lnd.main import create_app

    with TestClient(create_app(), follow_redirects=False) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# the raw landing layer
# ---------------------------------------------------------------------------
@pytest.fixture
def db(db_engine: Engine) -> Iterator[Session]:
    """A session over the raw layer, inside a transaction that is rolled back.

    Builds on `db_engine` above, so there is one decision about database
    reachability rather than two. The raw table is created if the database has
    not had migrations applied, so the landing tests run against a bare dev
    database as well as a migrated one.

    Starts from empty: a dev database carries rows from real syncs, and a test
    asserting "exactly one record" would otherwise pass or fail depending on
    what someone did in the terminal an hour ago. TRUNCATE is transactional in
    PostgreSQL, so the rollback puts every existing row back.
    """
    from lnd.db import SCHEMAS, Base
    from lnd.ingest.models import RawRecord

    with db_engine.begin() as setup:
        for schema in SCHEMAS:
            setup.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
    Base.metadata.create_all(db_engine, tables=[RawRecord.__table__], checkfirst=True)

    connection = db_engine.connect()
    transaction = connection.begin()
    connection.execute(text("TRUNCATE raw.source_record RESTART IDENTITY"))

    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# ---------------------------------------------------------------------------
# the star schema and the transform
# ---------------------------------------------------------------------------
@pytest.fixture
def core_db(db_engine: Engine) -> Iterator[Session]:
    """A session over `raw`, `core`, `app` and `ops`, rolled back afterwards.

    The transform's tests need all four schemas at once — it reads `raw`,
    consults the `app` overlay, writes `core` and records into `ops` — and
    several of its guarantees are constraints rather than code: the fact grains,
    the one-current-version index on `dim_employee`, the total CHECK on
    `dim_session`. None of that can be demonstrated against SQLite, so this
    builds on `db_engine` and skips with it.

    Everything is truncated on the way in. A dev database carries rows from
    real syncs, and a test asserting "exactly two attendance rows" would
    otherwise pass or fail depending on what someone did in the terminal an
    hour ago. TRUNCATE is transactional in PostgreSQL, so the rollback puts
    every existing row back.
    """
    from lnd.db import SCHEMAS, Base
    from lnd.models import (  # noqa: F401 - importing registers the tables
        DimProgram,
        DqException,
        ProgramOverride,
    )

    with db_engine.begin() as setup:
        for schema in SCHEMAS:
            setup.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
    Base.metadata.create_all(db_engine, checkfirst=True)

    connection = db_engine.connect()
    transaction = connection.begin()
    # CASCADE because the facts reference the dimensions; RESTART IDENTITY so
    # surrogate keys are predictable within a test.
    connection.execute(
        text(
            "TRUNCATE raw.source_record, core.dim_date, core.dim_trainer, "
            "core.dim_employee, core.dim_program, core.dim_session, "
            "core.fact_enrollment, core.fact_attendance, core.fact_evaluation, "
            "app.enrichment_program_override, app.trainer_alias, "
            "app.survey_question_map, app.survey_option_score, "
            "app.identity_mapping, ops.dq_exception "
            "RESTART IDENTITY CASCADE"
        )
    )

    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def loaded(core_db: Session) -> Session:
    """One programme transformed, with the question map seeded and one leaver.

    Shared rather than defined beside the metric tests, because the week-7
    views are checked against the same numbers. Two datasets would let a
    scorecard test pass against a shape the metric tests never see — and the
    whole claim those views make is that they show the same figures.
    """
    from tests.fixtures import metrics_dataset

    return metrics_dataset.build(core_db)
