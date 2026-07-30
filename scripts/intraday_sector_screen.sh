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

# Load KIS credentials (cron doesn't source .zshrc)
if [ -f ~/.hermes/profiles/work/.env ]; then
    set -a
    source ~/.hermes/profiles/work/.env
    set +a
fi

.venv/bin/python scripts/intraday_sector_screener.py --mode combined --top-n 3
