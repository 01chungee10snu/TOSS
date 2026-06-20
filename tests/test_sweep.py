"""Tests for parameter sweep / performance comparison loop."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from toss_alpha.daily.replay import _make_test_panel  # we'll add this helper
from toss_alpha.daily.sweep import run_sweep, SweepConfig


def _panel() -> pd.DataFrame:
    return _make_test_panel(days=120, n_symbols=5)


def test_sweep_runs_multiple_configs_and_returns_comparison():
    panel = _panel()
    configs = [
        SweepConfig(name="baseline", step=5, score_threshold=70.0),
        SweepConfig(name="aggressive", step=5, score_threshold=60.0),
        SweepConfig(name="selective", step=10, score_threshold=75.0),
    ]
    result = run_sweep(panel=panel, configs=configs, initial_cash_krw=1_000_000)

    assert len(result["runs"]) == 3
    for run in result["runs"]:
        assert "name" in run
        assert "summary" in run
        assert "total_return_pct" in run["summary"]
        assert "max_drawdown_pct" in run["summary"]
        assert "sharpe_ratio" in run["summary"]
        assert "win_rate_pct" in run["summary"]


def test_sweep_identifies_best_config_by_metric():
    panel = _panel()
    configs = [
        SweepConfig(name="low_thresh", step=5, score_threshold=50.0),
        SweepConfig(name="high_thresh", step=5, score_threshold=80.0),
    ]
    result = run_sweep(panel=panel, configs=configs, initial_cash_krw=1_000_000)

    assert "best" in result
    assert result["best"]["name"]  # has a name
    assert result["best"]["metric"]  # which metric was used
    assert isinstance(result["best"]["summary"]["sharpe_ratio"], float)


def test_run_sweep_writes_artifacts(tmp_path: Path):
    panel = _panel()
    configs = [
        SweepConfig(name="baseline", step=5, score_threshold=70.0),
        SweepConfig(name="alt", step=10, score_threshold=65.0),
    ]
    result = run_sweep(
        panel=panel,
        configs=configs,
        initial_cash_krw=1_000_000,
        out_dir=tmp_path / "sweep",
    )

    assert Path(result["comparison_csv"]).exists()
    assert Path(result["report_md"]).exists()

    import csv
    with open(result["comparison_csv"]) as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert "name" in rows[0]
    assert "total_return_pct" in rows[0]
    assert "sharpe_ratio" in rows[0]


def test_sweep_grid_generates_configs():
    from toss_alpha.daily.sweep import build_grid_configs

    grid = build_grid_configs(
        steps=[5, 10],
        score_thresholds=[65.0, 70.0, 75.0],
    )
    assert len(grid) == 6  # 2 steps × 3 thresholds
    names = [c.name for c in grid]
    assert any("s5_t65" in n for n in names)
    assert any("s10_t75" in n for n in names)


def test_profit_max_grid_generates_trailing_positions_and_sizing_axes():
    from toss_alpha.daily.sweep import build_grid_configs

    grid = build_grid_configs(
        steps=[10],
        score_thresholds=[65.0],
        stop_losses=[0.10],
        take_profits=[0.15],
        max_holding_steps=[20],
        max_positions=[1, 3],
        trailing_stops=[0.0, 0.08],
        sizing_modes=["flat", "score_weighted"],
    )

    assert len(grid) == 8
    assert {c.max_positions for c in grid} == {1, 3}
    assert {c.trailing_stop_pct for c in grid} == {0.0, 0.08}
    assert {c.sizing_mode for c in grid} == {"flat", "score_weighted"}
    assert any("mp3" in c.name and "tr8" in c.name and "score_weighted" in c.name for c in grid)
