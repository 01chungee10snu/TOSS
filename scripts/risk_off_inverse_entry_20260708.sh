#!/usr/bin/env bash
set -euo pipefail
cd /Users/01chungee10/Github/TOSS
export PYTHONPATH=src
export KIS_ACNT_PRDT_CD=01
export KIS_ACCOUNT_PRODUCT_CODE=01
export TOSS_REQUIRE_CURRENT_ISSUE_REPORT=true
.venv/bin/python scripts/risk_off_inverse_entry_20260708.py
