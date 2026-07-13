#!/usr/bin/env bash
set -euo pipefail
cd /Users/01chungee10/Github/TOSS
export PYTHONPATH=src
export BROKER_PROVIDER=kis
export KIS_ACNT_PRDT_CD=01
export KIS_ACCOUNT_PRODUCT_CODE=01
export KIS_RATE_LIMIT_STATE_PATH=${KIS_RATE_LIMIT_STATE_PATH:-/Users/01chungee10/Github/TOSS/reports/harness/kis_api_rate_limit_state.json}
export KIS_RATE_LIMIT_AUDIT_PATH=${KIS_RATE_LIMIT_AUDIT_PATH:-/Users/01chungee10/Github/TOSS/reports/harness/kis_api_rate_limit_audit.jsonl}
export KIS_ACCESS_TOKEN_CACHE=${KIS_ACCESS_TOKEN_CACHE:-/Users/01chungee10/Github/TOSS/reports/harness/kis_access_token_cache.json}
.venv/bin/python scripts/toss_intraday_realtime_guard.py --mode decision
