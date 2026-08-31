"""Baseline: the four schemas, extensions, and the grants that make raw immutable.

Revision ID: 0001
Revises:
Created: week 1

BRD §7.2 separates the database into four schemas, and §7.3 requires the raw
layer to be immutable — CRM, Graph, HRIS and LinkedIn responses are stored
exactly as received so that when a number looks wrong, the stored payload proves
whether it arrived wrong or the transform broke it.

"Immutable" is a property here, not a rule people remember. The application
connects as a login role that is a member of `lnd_app`, and `lnd_app` is granted
SELECT and INSERT on raw but never UPDATE or DELETE. An UPDATE against raw does
not fail review; it fails at the database.

    raw    append-only source payloads, jsonb + payload_hash
    core   the star schema — a pure function of (raw + enrichment)
    app    enrichment overrides and other human-authored state
    ops    sync_run, dq_exception, alembic_version — operational furniture
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMAS = ("raw", "core", "app", "ops")

APP_ROLE = "lnd_app"


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    # citext: employee email is a match key in identity resolution and case must
    #         not create a second person.
    # pg_trgm: the fourth resolution step is a fuzzy name suggestion a human
    #          confirms; trigram similarity is what ranks the candidates.
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # A NOLOGIN group role. The per-environment login role is created by
    # docker/db/01-app-role.sh and granted membership below.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN;
            END IF;
        END
        $$;
        """
    )

    op.execute(f"GRANT USAGE ON SCHEMA {', '.join(SCHEMAS)} TO {APP_ROLE}")

    # --- raw: read and append, never amend --------------------------------
    op.execute(f"GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA raw TO {APP_ROLE}")
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA raw FROM {APP_ROLE}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA raw GRANT SELECT, INSERT ON TABLES TO {APP_ROLE}"
    )
    op.execute(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA raw TO {APP_ROLE}")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA raw GRANT USAGE ON SEQUENCES TO {APP_ROLE}")

    # --- core, app, ops: full DML, still no DDL ----------------------------
    for schema in ("core", "app", "ops"):
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO {APP_ROLE}"
        )
        op.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
        )
        op.execute(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA {schema} TO {APP_ROLE}")
        op.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT USAGE ON SEQUENCES TO {APP_ROLE}"
        )

    # Nothing is granted on `public`, and it is left without a create grant so
    # no table can be made outside the four schemas by accident.
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")

    # Every login role that exists now becomes a member; 01-app-role.sh has
    # already created it by the time migrations run.
    op.execute(
        f"""
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN
                SELECT rolname FROM pg_roles
                WHERE rolcanlogin
                  AND NOT rolsuper
                  AND rolname NOT IN ('{APP_ROLE}')
                  AND rolname NOT LIKE 'pg\\_%'
            LOOP
                EXECUTE format('GRANT {APP_ROLE} TO %I', r.rolname);
            END LOOP;
        END
        $$;
        """
    )


def downgrade() -> None:
    # Dropping the schemas would take the warehouse with them. The baseline is
    # deliberately not reversible: rebuilding from empty means a fresh database,
    # not a downgrade.
    raise RuntimeError(
        "0001 is the baseline and cannot be downgraded. Recreate the database instead."
    )
