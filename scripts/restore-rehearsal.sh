#!/usr/bin/env bash
# The week-10 rehearsal: back up, restore somewhere else, and prove the copy is
# the same platform.
#
#   scripts/restore-rehearsal.sh
#
# The plan has said since week 2 that restore-from-backup is configured and not
# proven, and the runbook has refused to claim otherwise. This is what replaces
# that paragraph, and it is a script rather than a procedure so that it can be
# run again next quarter by somebody who was not here.
#
# WHAT "PROVED" MEANS HERE
#
# Not that the file restored without an error — pg_restore says that about a
# database missing every grant it had. Three things are checked afterwards, and
# the third is the one that matters:
#
#   1. Every grain has the same row count as the source.
#   2. `raw` is still append-only for the application role. Restoring the data
#      and losing the access control would leave a platform that looks identical
#      and no longer guarantees the thing it was built to guarantee.
#   3. The published figures are unchanged, computed out of the restored
#      database by the same registry the dashboard reads.

set -euo pipefail

COMPOSE=${COMPOSE:-"-f compose.yaml -f compose.dev.yaml"}
TARGET=${TARGET:-lnd_restore}
# Written here rather than by the container: the verifier runs as uid 10001
# against a repository owned by somebody else, so it prints the report and this
# redirects it, leaving a file owned by whoever ran the rehearsal.
REPORT=${REPORT:-docs/restore-rehearsal.md}
export COMPOSE

env_value() { grep -E "^$1=" .env | head -1 | cut -d= -f2-; }
PGUSER=$(env_value POSTGRES_USER)
PGPASS=$(env_value POSTGRES_PASSWORD)
LIVE=$(env_value POSTGRES_DB)

# The whole operation, not the verification. "How long am I down for" is asked
# during the incident, and answering it with the fastest third would be worse
# than not measuring it.
started=$(date +%s)

echo "══ 1. back up ═════════════════════════════════════════════════════════"
scripts/backup.sh
DUMP=$(ls -1t backups/dumps/*.dump | head -1)

echo
echo "══ 2. restore into $TARGET ════════════════════════════════════════════"
scripts/restore.sh "$DUMP" "$TARGET"

echo
echo "══ 3. verify ══════════════════════════════════════════════════════════"
docker compose $COMPOSE exec -T \
  -e DATABASE_URL="postgresql+psycopg://${PGUSER}:${PGPASS}@db:5432/${TARGET}" \
  -e RESTORE_SOURCE_URL="postgresql+psycopg://${PGUSER}:${PGPASS}@db:5432/${LIVE}" \
  -e RESTORE_DUMP="$(basename "$DUMP")" \
  -e RESTORE_TARGET="$TARGET" \
  -e RESTORE_SECONDS="$(( $(date +%s) - started ))" \
  api python -m lnd.reference.restore > "$REPORT"
echo "✓ wrote $REPORT"
