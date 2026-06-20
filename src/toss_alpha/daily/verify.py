"""Verification loops for replay candidates: cost stress and yearly splits.

Paper/research only. No live orders.
"""
from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from toss_alpha.daily.replay import ReplayEngine

DEFAULT_VERIFY_DIR = Path("reports/verify")


def _symbols(panel: pd.DataFrame) -> list[str]:
    return sorted(panel["code"].astype(str).str.zfill(6).unique().tolist())


def _run(panel: pd.DataFrame, config: dict[str, Any], *, initial_cash_krw: float = 1_000_000, cost_bps: float = 0.0) -> dict[str, Any]:
    cfg = dict(config)
    step = int(cfg.pop("step"))
    engine = ReplayEngine(
        panel=panel,
        symbols=_symbols(panel),
        initial_cash_krw=initial_cash_krw,
        transaction_cost_bps=cost_bps,
        **cfg,
    )
    return engine.run(step=step)


def run_cost_stress(
    *,
    panel: pd.DataFrame,
    config: dict[str, Any],
    cost_bps_values: list[float],
    initial_cash_krw: float = 1_000_000,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run one config under multiple transaction cost assumptions."""
    verify_dir = Path(out_dir) if out_dir else DEFAULT_VERIFY_DIR
    verify_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for cost_bps in cost_bps_values:
        result = _run(panel, config, initial_cash_krw=initial_cash_krw, cost_bps=cost_bps)
        s = result["summary"]
        rows.append({
            "cost_bps": cost_bps,
            "total_return_pct": s["total_return_pct"],
            "max_drawdown_pct": s["max_drawdown_pct"],
            "sharpe_ratio": s["sharpe_ratio"],
            "win_rate_pct": s["win_rate_pct"],
            "total_trades": s["total_trades"],
            "final_equity_krw": s["final_equity_krw"],
            "total_cost_krw": s.get("total_cost_krw", 0.0),
        })

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    csv_path = verify_dir / f"cost_stress_{ts}.csv"
    _write_rows(csv_path, rows)
    return {"rows": rows, "csv_path": str(csv_path)}


def run_yearly_split(
    *,
    panel: pd.DataFrame,
    config: dict[str, Any],
    initial_cash_krw: float = 1_000_000,
    cost_bps: float = 0.0,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run one config independently on each calendar year."""
    verify_dir = Path(out_dir) if out_dir else DEFAULT_VERIFY_DIR
    verify_dir.mkdir(parents=True, exist_ok=True)

    p = panel.copy()
    p["Date"] = pd.to_datetime(p["Date"])
    rows: list[dict[str, Any]] = []
    for year in sorted(p["Date"].dt.year.unique().tolist()):
        yp = p[p["Date"].dt.year == year].copy()
        if yp.empty:
            continue
        result = _run(yp, config, initial_cash_krw=initial_cash_krw, cost_bps=cost_bps)
        s = result["summary"]
        rows.append({
            "year": int(year),
            "cost_bps": cost_bps,
            "total_return_pct": s["total_return_pct"],
            "max_drawdown_pct": s["max_drawdown_pct"],
            "sharpe_ratio": s["sharpe_ratio"],
            "win_rate_pct": s["win_rate_pct"],
            "total_trades": s["total_trades"],
            "final_equity_krw": s["final_equity_krw"],
            "total_cost_krw": s.get("total_cost_krw", 0.0),
        })

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    csv_path = verify_dir / f"yearly_split_{ts}.csv"
    _write_rows(csv_path, rows)
    return {"rows": rows, "csv_path": str(csv_path)}


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
