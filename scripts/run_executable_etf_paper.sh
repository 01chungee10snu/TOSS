#!/bin/bash
set -euo pipefail

REPO="/Users/01chungee10/Github/TOSS"
cd "$REPO"

# launchd does not source the interactive shell profile. Load the same private
# environment used by the existing KIS operational wrappers when available.
if [ -f "$HOME/.hermes/profiles/work/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$HOME/.hermes/profiles/work/.env"
    set +a
fi

export PYTHONPATH="$REPO/src"
export KIS_ACCESS_TOKEN_CACHE="${KIS_ACCESS_TOKEN_CACHE:-$REPO/reports/harness/kis_access_token_cache.json}"
export KIS_RATE_LIMIT_AUDIT_PATH="${KIS_RATE_LIMIT_AUDIT_PATH:-$REPO/reports/harness/kis_api_rate_limit_audit.jsonl}"

# Hard invariant for this scheduled job: research/read-only collection only.
# The Python script imports KisReadOnlyClient and contains no order submission.
exec "$REPO/.venv/bin/python" "$REPO/scripts/run_executable_etf_paper.py"
