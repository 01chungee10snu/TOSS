#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/01chungee10/Github/TOSS"
cd "$ROOT"

export PYTHONPATH=src

exec .venv/bin/python scripts/paper_portfolio.py
