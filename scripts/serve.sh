#!/usr/bin/env bash
# Serve the pages from the repo root whatever the caller's working directory is (used by .claude/launch.json).
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
. .venv/bin/activate
# Local settings (AUXO_LLM and friends, see .env.example); gitignored, optional.
if [ -f .env ]; then set -a; . ./.env; set +a; fi
exec python run.py serve --port "${PORT:-8000}"
