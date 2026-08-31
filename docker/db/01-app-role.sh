#!/bin/sh
# Runs once, on first initialisation of the Postgres data volume.
#
# Creates the least-privilege login role that api, worker and beat connect as.
# It deliberately owns nothing: migration 0001 grants it membership of the
# `lnd_app` group role, which carries SELECT and INSERT on raw.* but no UPDATE
# and no DELETE. That is what makes raw immutability a property of the database
# rather than a rule people remember.
#
# Migrations connect as POSTGRES_USER (the owner) instead, because DDL needs it.
set -eu

if [ -z "${APP_DB_USER:-}" ] || [ -z "${APP_DB_PASSWORD:-}" ]; then
  echo "01-app-role: APP_DB_USER / APP_DB_PASSWORD not set, skipping" >&2
  exit 0
fi

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname   "$POSTGRES_DB" \
     -v app_user="$APP_DB_USER" \
     -v app_password="$APP_DB_PASSWORD" <<'SQL'
SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L',
  :'app_user', :'app_password'
) AS stmt
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')
\gexec

SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'app_user') AS stmt
\gexec
SQL

echo "01-app-role: login role ${APP_DB_USER} ready"
