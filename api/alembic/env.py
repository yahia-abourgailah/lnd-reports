"""Alembic environment.

The connection string is never written down here — it comes from DATABASE_URL,
like every other environment-specific value (NFR-11). In a deploy this runs as a
one-shot container before anything else is recreated, so a failed migration
leaves the running version untouched.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Imported for its side effect: the package registers every model on
# Base.metadata. Without it autogenerate sees an empty model set and proposes
# dropping the tables that are actually there.
import lnd.models  # noqa: F401
from lnd.db import SCHEMAS, Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError("DATABASE_URL is not set; alembic has nothing to connect to.")
config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def include_name(name: str | None, type_: str, _parent: dict[str, object]) -> bool:
    """Keep autogenerate inside our own schemas.

    Without this, `alembic revision --autogenerate` proposes dropping every
    table it can see that is not in the metadata — including anything else
    sharing the database.
    """
    if type_ == "schema":
        return name in SCHEMAS
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        include_name=include_name,
        version_table_schema="ops",
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # alembic_version lives in `ops` with the rest of the operational
        # furniture, but that schema does not exist on a virgin database — so it
        # is created here, before the version table is consulted.
        connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS ops")
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_name=include_name,
            version_table_schema="ops",
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
