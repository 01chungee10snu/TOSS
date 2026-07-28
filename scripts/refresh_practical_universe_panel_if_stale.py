#!/usr/bin/env python3
"""Check if the practical_universe_400 panel is stale and refresh if needed.

Mirrors refresh_random500_panel_if_stale.py but targets the practical universe panel
that the live trading loop actually uses (TOSS_PANEL_CSV).

If the panel's latest trading day is within TOSS_PANEL_REFRESH_MAX_TRADING_DAY_LAG
(default 1) of today, skip refresh. Otherwise run update_practical_universe_panel_2026.py.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from toss_alpha.execution.krx_calendar import is_krx_trading_day  # noqa: E402

PANEL_CSV = ROOT / "reports" / "backtests" / "practical_universe_400_2022-01-01_2026-latest_ohlcv_panel.csv"
STATUS_JSON = ROOT / "reports" / "harness" / "practical_universe_panel_refresh_status.json"
UPDATE_SCRIPT = ROOT / "scripts" / "update_practical_universe_panel_2026.py"


def previous_or_same_trading_day(day: date) -> date:
    cur = day
    for _ in range(14):
        if is_krx_trading_day(cur):
            return cur
        cur -= timedelta(days=1)
    raise RuntimeError("could not resolve recent KRX trading day")


def trading_day_lag(panel_date: date, target_date: date) -> int:
    if panel_date > target_date:
        return 0
    lag = 0
    cur = panel_date
    while cur < target_date:
        cur += timedelta(days=1)
        if is_krx_trading_day(cur):
            lag += 1
    return lag


def latest_panel_date(path: Path) -> date | None:
    if not path.exists():
        return None
    df = pd.read_csv(path, usecols=["Date"])
    if df.empty:
        return None
    return pd.to_datetime(df["Date"]).max().date()


def write_status(payload: dict[str, Any]) -> None:
    STATUS_JSON.parent.mkdir(parents=True, exist_ok=True)
    STATUS_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main() -> int:
    max_lag = int(float(__import__("os").environ.get("TOSS_PANEL_REFRESH_MAX_TRADING_DAY_LAG") or "1"))
    target = previous_or_same_trading_day(date.today())
    before = latest_panel_date(PANEL_CSV)
    lag = None if before is None else trading_day_lag(before, target)
    status: dict[str, Any] = {
        "panel_csv": str(PANEL_CSV),
        "target_trading_day": target.isoformat(),
        "latest_before": before.isoformat() if before else None,
        "max_allowed_lag": max_lag,
        "lag_before": lag,
        "refreshed": False,
    }
    if before is not None and lag is not None and lag <= max_lag:
        status["status"] = "FRESH_ENOUGH"
        write_status(status)
        print(f"PRACTICAL_PANEL_REFRESH_STATUS=FRESH_ENOUGH latest={before} lag={lag}")
        return 0

    step = subprocess.run([sys.executable, str(UPDATE_SCRIPT)], cwd=ROOT, text=True, capture_output=True, check=False)
    after = latest_panel_date(PANEL_CSV)
    after_lag = None if after is None else trading_day_lag(after, target)
    refreshed = after is not None and (before is None or after > before)
    status.update({
        "step_stdout": step.stdout[-2000:] if step.stdout else "",
        "step_stderr": step.stderr[-2000:] if step.stderr else "",
        "step_exit_code": step.returncode,
        "latest_after": after.isoformat() if after else None,
        "lag_after": after_lag,
        "refreshed": refreshed,
        "status": "REFRESHED" if refreshed else "REFRESH_FAILED",
    })
    write_status(status)
    if refreshed:
        print(f"PRACTICAL_PANEL_REFRESH_STATUS=REFRESHED latest={after} lag={after_lag}")
    else:
        print(f"PRACTICAL_PANEL_REFRESH_STATUS=FAILED latest={after} lag={after_lag}")
    return 0 if refreshed else 1


if __name__ == "__main__":
    raise SystemExit(main())
