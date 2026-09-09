#!/usr/bin/env bash
# Restore a dump into a named database, and refuse to do it anywhere dangerous.
#
#   scripts/restore.sh backups/dumps/lnd-20260909T110000Z.dump lnd_restore
#
# The second argument is mandatory and is checked against the live database
# name. There is no flag to override it. Restoring over a running database is a
# thing somebody does deliberately, with the stack stopped, by renaming the
# database first — not a thing a script offers as a convenience at three in the
# morning.
#
# WHAT A RESTORE ACTUALLY HAS TO REBUILD
#
# The roles. `lnd_app_rw` and the `lnd_app` group live in the cluster, not in
# the database, so a dump restored into a fresh cluster arrives with every grant
# referring to a role that does not exist — and `raw` comes back writable by
# nobody, or by everybody, depending on which error was ignored. Migration 0001
# creates them, so on a fresh cluster the order is: create the roles, then
# restore. This script checks and says so rather than discovering it.

set -euo pipefail

COMPOSE=${COMPOSE:-"-f compose.yaml -f compose.dev.yaml"}

DUMP=${1:-}
TARGET=${2:-}
if [ -z "$DUMP" ] || [ -z "$TARGET" ]; then
  echo "usage: scripts/restore.sh <dump file> <target database>" >&2
  exit 2
fi
[ -f "$DUMP" ] || { echo "✗ no such dump: $DUMP" >&2; exit 2; }

env_value() { grep -E "^$1=" .env | head -1 | cut -d= -f2-; }
PGUSER=$(env_value POSTGRES_USER)
LIVE=$(env_value POSTGRES_DB)

if [ "$TARGET" = "$LIVE" ]; then
  echo "✗ $TARGET is the live database." >&2
  echo "  Restore into a scratch name, verify it, then rename. There is no flag." >&2
  exit 1
fi

psql_() { docker compose $COMPOSE exec -T db psql -U "$PGUSER" -d "${1:-postgres}" "${@:2}"; }

echo "→ target $TARGET"
if psql_ postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$TARGET'" | grep -q 1; then
  echo "  dropping and recreating $TARGET"
  psql_ postgres -c "DROP DATABASE \"$TARGET\"" >/dev/null
fi
docker compose $COMPOSE exec -T db createdb -U "$PGUSER" "$TARGET"

# The roles the grants refer to. Present already on this cluster; named here so
# a restore into a fresh one fails loudly on the next line instead of silently
# restoring a database whose access control did not survive.
for role in lnd_app lnd_app_rw; do
  psql_ postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='$role'" | grep -q 1 || {
    echo "✗ role $role does not exist on this cluster." >&2
    echo "  Run migration 0001 against the target first — it creates the roles" >&2
    echo "  and the default privileges the dump's grants refer to." >&2
    exit 1
  }
done

echo "→ restoring $(basename "$DUMP")"
# --exit-on-error, so a half-restored database is never reported as restored.
# Ownership and grants come from the dump: the point of the rehearsal is to find
# out whether they do.
docker compose $COMPOSE exec -T db \
  pg_restore -U "$PGUSER" -d "$TARGET" --exit-on-error --no-password < "$DUMP"

echo "✓ restored into $TARGET"
