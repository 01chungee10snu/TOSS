#!/usr/bin/env bash
set -euo pipefail
cd /Users/01chungee10/Github/TOSS
export PYTHONPATH=src
.venv/bin/python scripts/audit_strategic_live_decision_harness.py
