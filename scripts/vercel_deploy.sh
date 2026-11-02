#!/usr/bin/env bash
# Build a slim Vercel bundle outside the repo (code, templates, a gzipped snapshot of the database and its models,
# the two files the leadership page reads) and deploy it as a preview. Pass --prod for production.
# --build-only stops after the bundle. Needs `npx vercel login` once. Nothing in the repo or data/ is changed.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${AUXO_VERCEL_OUT:-${TMPDIR:-/tmp}/auxocase-vercel}"
DB="${AUXO_DB:-data/auxocase.db}"
rm -rf "$OUT"; mkdir -p "$OUT/seed" "$OUT/docs" "$OUT/outputs"
cp deploy/vercel/index.py deploy/vercel/vercel.json deploy/vercel/requirements.txt deploy/vercel/.python-version "$OUT/"
rsync -a --exclude '__pycache__' src "$OUT/"
cp docs/evaluation.json "$OUT/docs/" 2>/dev/null || true
cp outputs/queue_limits.json "$OUT/outputs/" 2>/dev/null || true
cp -R "$(dirname "$DB")/models" "$OUT/seed/models"
# A consistent copy even while the local server writes (the backup API), then one file, no WAL, gzipped.
.venv/bin/python - "$DB" "$OUT/seed/auxocase.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.execute("PRAGMA journal_mode=DELETE"); dst.execute("VACUUM"); dst.close()
PY
gzip -6 "$OUT/seed/auxocase.db"
[ -d .vercel ] && cp -R .vercel "$OUT/"          # keep deploying to the same project once linked
echo "bundle: $OUT ($(du -sh "$OUT" | cut -f1))"
if [ "${1:-}" = "--build-only" ]; then exit 0; fi
cd "$OUT"
[ -d .vercel ] || npx -y vercel@latest link --yes --project "${AUXO_VERCEL_PROJECT:-auxocase}"
npx -y vercel@latest deploy --yes "$@"
[ -d "$OUT/.vercel" ] && rm -rf "$OLDPWD/.vercel" && cp -R "$OUT/.vercel" "$OLDPWD/"
