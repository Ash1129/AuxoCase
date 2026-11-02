#!/usr/bin/env bash
# Deploy to Vercel. Pushes to GitHub deploy on their own; this script is for refreshing the data and for deploying
# from this machine without a push.
#   scripts/vercel_deploy.sh            snapshot the database, upload it, deploy a preview from this folder
#   scripts/vercel_deploy.sh --data     only snapshot and upload (the next deployment or cold start picks it up)
#   scripts/vercel_deploy.sh --prod     snapshot, upload, deploy to production
# The snapshot (database, model pickles, queue-limits study) goes to the project's private Blob store at a fixed
# path; AUXO_SNAPSHOT_URL names it for asgi.py. Needs `npx vercel login` and `npx vercel link` once.
set -euo pipefail
cd "$(dirname "$0")/.."
DB="${AUXO_DB:-data/auxocase.db}"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
VC="npx -y vercel@latest"
# A consistent copy even while the local server writes (the backup API), then one file, no WAL.
.venv/bin/python - "$DB" "$WORK/auxocase.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.execute("PRAGMA journal_mode=DELETE"); dst.execute("VACUUM"); dst.close()
PY
cp -R "$(dirname "$DB")/models" "$WORK/models"
[ -f outputs/queue_limits.json ] && cp outputs/queue_limits.json "$WORK/"
tar -czf "$WORK/snapshot.tar.gz" -C "$WORK" auxocase.db models $( [ -f "$WORK/queue_limits.json" ] && echo queue_limits.json )
echo "snapshot: $(du -h "$WORK/snapshot.tar.gz" | cut -f1)"
[ -f .env.local ] || $VC env pull .env.local --yes >/dev/null
TOKEN="$(grep '^BLOB_READ_WRITE_TOKEN=' .env.local | cut -d= -f2- | tr -d '"')"
URL="$($VC blob put "$WORK/snapshot.tar.gz" --pathname snapshot/auxocase.tar.gz --access private --force \
        --cache-control-max-age 60 --rw-token "$TOKEN" 2>&1 | grep -o 'https://[^ ]*' | head -1)"
[ -n "$URL" ] || { echo "upload failed" >&2; exit 1; }
if ! $VC env ls 2>/dev/null | grep -q AUXO_SNAPSHOT_URL; then
  for e in production preview development; do
    $VC env add AUXO_SNAPSHOT_URL --environment "$e" --no-sensitive --value "$URL" --yes >/dev/null
  done
fi
echo "uploaded: $URL"
[ "${1:-}" = "--data" ] && exit 0
$VC deploy --yes "$@"
