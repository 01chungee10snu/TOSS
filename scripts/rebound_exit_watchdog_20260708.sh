#!/usr/bin/env bash
set -euo pipefail
cd /Users/01chungee10/Github/TOSS
export PYTHONPATH=src

# Historical one-off launcher: permanently fail closed. The Python entrypoint
# is also quarantined, so inherited credentials cannot result in a broker call.
export TOSS_LEGACY_LIVE_QUARANTINED=true
export TOSS_RISK_LIVE_TRADING_ENABLED=false
export KIS_LIVE_TRADING_ENABLED=false
export TOSS_LIVE_SUBMIT_ENABLED=false
export TOSS_LIVE_SUBMIT_DRY_RUN=true
unset TOSS_LIVE_SUBMIT_CONFIRMATION || true

exec .venv/bin/python scripts/rebound_exit_watchdog_20260708.py
