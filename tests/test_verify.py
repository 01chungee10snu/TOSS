"""Tests for verification/stress loops around promoted replay candidates."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from toss_alpha.daily.replay import ReplayEngine, _make_test_panel
from toss_alpha.daily.verify import run_cost_stress, run_yearly_split


def _frontier_config() -> dict:
    return {
        "step": 5,
        "score_threshold": 55.0,
        "stop_loss_pct": 0.12,
        "take_profit_pct": 0.20,
        "max_holding_steps": 10,
        "max_positions": 4,
        "trailing_stop_pct": 0.0,
        "sizing_mode": "flat",
    }


def test_replay_engine_cost_bps_reduces_equity():
    panel = _make_test_panel(days=160, n_symbols=8)
    symbols = sorted(panel["code"].astype(str).unique().tolist())

    cfg = _frontier_config()
    step = cfg.pop("step")
    no_cost = ReplayEngine(panel=panel, symbols=symbols, initial_cash_krw=1_000_000, transaction_cost_bps=0.0, **cfg).run(step=step)
    with_cost = ReplayEngine(panel=panel, symbols=symbols, initial_cash_krw=1_000_000, transaction_cost_bps=30.0, **cfg).run(step=step)

    assert with_cost["summary"]["final_equity_krw"] <= no_cost["summary"]["final_equity_krw"]
    assert with_cost["summary"]["total_cost_krw"] > 0


def test_run_cost_stress_returns_rows_for_each_bps(tmp_path: Path):
    panel = _make_test_panel(days=160, n_symbols=8)
    result = run_cost_stress(
        panel=panel,
        config=_frontier_config(),
        cost_bps_values=[0, 10, 20, 30],
        out_dir=tmp_path / "verify",
    )

    assert len(result["rows"]) == 4
    assert Path(result["csv_path"]).exists()
    assert result["rows"][0]["cost_bps"] == 0
    assert result["rows"][-1]["cost_bps"] == 30


def test_run_yearly_split_returns_one_row_per_year(tmp_path: Path):
    panel = _make_test_panel(start_date="2022-01-01", days=900, n_symbols=8)
    result = run_yearly_split(
        panel=panel,
        config=_frontier_config(),
        out_dir=tmp_path / "verify",
    )

    years = {row["year"] for row in result["rows"]}
    assert len(years) >= 2
    assert Path(result["csv_path"]).exists()
    for row in result["rows"]:
        assert "total_return_pct" in row
        assert "max_drawdown_pct" in row
        assert "total_trades" in row
