#!/usr/bin/env bash
# Nightly backup: one custom-format dump, and prune what is older than the
# retention window.
#
#   scripts/backup.sh                     # dev stack
#   COMPOSE="-f compose.yaml -f compose.prod.yaml" scripts/backup.sh
#
# Week 10. Until now `pg_dump -Fc` was a comment in compose.prod.yaml rather
# than a thing that runs: WAL archiving was configured and the base backup it
# needs was not, which is the failure mode where recovery looks provisioned and
# has no starting point. This is the missing half.
#
# WHY -Fc AND NOT PLAIN SQL
#
# Custom format is compressed, and `pg_restore` can read it selectively — one
# schema, one table, or a listing of what is inside without restoring anything.
# On a database whose whole value is the append-only raw layer, being able to
# restore `raw` alone is worth more than being able to read the file.
#
# WHY IT DUMPS AS THE OWNER
#
# `lnd_app_rw` cannot read what it cannot select, and by design it has no
# UPDATE on raw and no CREATE anywhere. A dump taken as the application role
# would be missing exactly the parts that matter and would restore into a
# database with no grants. The owner is the only role that can take a complete
# one.

set -euo pipefail

COMPOSE=${COMPOSE:-"-f compose.yaml -f compose.dev.yaml"}
BACKUP_DIR=${BACKUP_DIR:-./backups}
RETENTION_DAYS=${RETENTION_DAYS:-30}

env_value() { grep -E "^$1=" .env | head -1 | cut -d= -f2-; }

PGUSER=$(env_value POSTGRES_USER)
PGDATABASE=$(env_value POSTGRES_DB)
: "${PGUSER:?POSTGRES_USER is not set in .env}"
: "${PGDATABASE:?POSTGRES_DB is not set in .env}"

mkdir -p "$BACKUP_DIR/dumps"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
target="$BACKUP_DIR/dumps/${PGDATABASE}-${stamp}.dump"

echo "→ dumping $PGDATABASE as $PGUSER"
# Streamed to the host rather than written inside the container: a backup that
# lives on the same volume as the data it protects is not a backup.
docker compose $COMPOSE exec -T db \
  pg_dump -U "$PGUSER" -d "$PGDATABASE" -Fc --no-password > "$target"

size=$(wc -c < "$target")
if [ "$size" -lt 1024 ]; then
  echo "✗ dump is ${size} bytes — that is not a database. Keeping it for inspection." >&2
  exit 1
fi

# Readable by pg_restore is the only definition of a valid dump worth applying.
# A file that exists and cannot be listed is the backup everybody discovers on
# the day they need it.
#
# Via a temporary file rather than `pg_restore --list /dev/stdin`: the custom
# format is seekable and a pipe is not, so reading it from stdin fails with
# "did not find magic string in file header" on a dump that is perfectly good —
# a verification step that fails on valid backups is worse than none.
docker compose $COMPOSE exec -T db sh -c \
  'cat > /tmp/verify.dump && pg_restore --list /tmp/verify.dump > /dev/null && rm -f /tmp/verify.dump' \
  < "$target"
echo "✓ $target ($(( size / 1024 )) KB, listing verified)"

echo "→ pruning dumps older than ${RETENTION_DAYS} days"
find "$BACKUP_DIR/dumps" -name '*.dump' -mtime "+${RETENTION_DAYS}" -print -delete || true
echo "✓ $(find "$BACKUP_DIR/dumps" -name '*.dump' | wc -l) dumps retained"
