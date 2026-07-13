#!/usr/bin/env bash
set -euo pipefail
cd /Users/01chungee10/Github/TOSS
export PYTHONPATH=src
export KIS_ACNT_PRDT_CD=01
export KIS_ACCOUNT_PRODUCT_CODE=01
export TOSS_RISK_LIVE_TRADING_ENABLED=true
export KIS_LIVE_TRADING_ENABLED=true
export TOSS_LIVE_SUBMIT_ENABLED=true
export TOSS_LIVE_SUBMIT_DRY_RUN=false
export TOSS_LIVE_SUBMIT_CONFIRMATION='I UNDERSTAND THIS IS A REAL ORDER'
export TOSS_MAX_ORDER_KRW=1000000
export TOSS_MAX_POSITION_PCT=1.0
export TOSS_ALLOW_QUAL_DATA_BLOCKED=true
export TOSS_LIVE_STRATEGY_ID=manual_rebound_exit_watchdog_20260708
.venv/bin/python scripts/rebound_exit_watchdog_20260708.py
