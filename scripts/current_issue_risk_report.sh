#!/usr/bin/env bash
set -euo pipefail
cd /Users/01chungee10/Github/TOSS
export PYTHONPATH=src
.venv/bin/python scripts/current_issue_risk_report.py
