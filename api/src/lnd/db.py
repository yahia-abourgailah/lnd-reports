"""Database engine and session.

Synchronous SQLAlchemy 2.0 on purpose. The API, the Celery worker and the beat
scheduler share one code path, so the query behind a scheduled monthly report is
literally the same function as the one behind the dashboard. At 15,000
attendance records the asynchronous variant buys nothing and would force two
engines and two spellings of every query.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from lnd.config import get_settings

# The four schemas from BRD §7.2. `raw` is append-only and enforced by grant,
# not by convention — see alembic/versions/0001.
SCHEMA_RAW = "raw"
SCHEMA_CORE = "core"
SCHEMA_APP = "app"
SCHEMA_OPS = "ops"
SCHEMAS = (SCHEMA_RAW, SCHEMA_CORE, SCHEMA_APP, SCHEMA_OPS)


class Base(DeclarativeBase):
    """Declarative base. Every model states its schema explicitly."""


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,  # survive a Postgres restart without a 500
            pool_recycle=1800,
            echo=settings.db_echo,
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for workers and scripts."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency. Read paths commit nothing; writes commit explicitly."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def ping() -> bool:
    """True if the database answers. Used by the readiness probe."""
    with get_engine().connect() as conn:
        return bool(conn.execute(text("SELECT 1")).scalar_one() == 1)


def dispose_engine() -> None:
    """Drop pooled connections. Called on shutdown and between tests."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
