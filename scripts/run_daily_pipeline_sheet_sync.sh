#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/01chungee10/Github/TOSS"
cd "$ROOT"

export PYTHONPATH=src
export TOSS_ALPHA_DAILY_PIPELINE_EXPORT_TO_SHEETS=true
export TOSS_ALPHA_DAILY_PIPELINE_SHEET_ID="1rIawUGSPb0140dgBEom6BfIcG8MaxgYeuOuQBDcI4Ns"

exec .venv/bin/python scripts/run_daily_data_strategy_pipeline.py
