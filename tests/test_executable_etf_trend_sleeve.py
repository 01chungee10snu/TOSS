from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_executable_etf_trend_sleeve.py"


def load_module():
    spec = importlib.util.spec_from_file_location("etf_trend_sleeve_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.path.insert(0, str(ROOT / "scripts"))
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        try:
            sys.path.remove(str(ROOT / "scripts"))
        except ValueError:
            pass
    return module


def synthetic_panel(days: int = 320) -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-02", periods=days)
    # Equity rises, then falls enough to cross a long moving average.
    equity = np.concatenate([
        np.linspace(10_000.0, 16_000.0, days // 2),
        np.linspace(16_000.0, 8_000.0, days - days // 2),
    ])
    bond = np.linspace(100_000.0, 102_000.0, days)
    rows = []
    for code, values in [("226490", equity), ("153130", bond)]:
        for date, close in zip(dates, values):
            rows.append({
                "date": date,
                "code": code,
                "open": float(close) * 0.999,
                "close": float(close),
                "dividends": 0.0,
            })
    return pd.DataFrame(rows)


def test_signal_is_close_of_day_and_every_trade_executes_later():
    m = load_module()
    result = m.run_backtest(synthetic_panel(), variant="trend120_50_10", cost_bps=75)
    assert result.trades
    for trade in result.trades:
        assert pd.Timestamp(trade["signal_date"]) < pd.Timestamp(trade["date"])


def test_quantities_are_whole_nonnegative_shares():
    m = load_module()
    result = m.run_backtest(synthetic_panel(), variant="trend200_50_10", cost_bps=75)
    assert result.rebalances
    for rb in result.rebalances:
        for qty in rb["target_quantities"].values():
            assert isinstance(qty, int)
            assert qty >= 0


def test_variant_selection_ignores_holdout_metrics():
    m = load_module()
    base = [
        {
            "variant": "a",
            "cost_bps": 75,
            "train": {"total_return_pct": 10.0, "sharpe": 1.2, "cagr_pct": 8.0, "max_drawdown_pct": -10.0},
            "holdout": {"total_return_pct": -90.0, "sharpe": -10.0},
        },
        {
            "variant": "b",
            "cost_bps": 75,
            "train": {"total_return_pct": 9.0, "sharpe": 0.8, "cagr_pct": 7.0, "max_drawdown_pct": -8.0},
            "holdout": {"total_return_pct": 900.0, "sharpe": 10.0},
        },
    ]
    assert m.select_variant_train_only(base) == "a"
    base[0]["holdout"], base[1]["holdout"] = base[1]["holdout"], base[0]["holdout"]
    assert m.select_variant_train_only(base) == "a"


def test_pair_correlation_detects_identical_curves():
    m = load_module()
    dates = pd.bdate_range("2024-01-01", periods=20)
    curve = pd.DataFrame({"date": dates, "equity": 100.0 * np.cumprod(1.0 + np.linspace(-0.01, 0.01, 20))})
    corr = m.pair_correlation(curve, curve)
    assert corr["pearson"] is not None and corr["pearson"] > 0.999999
    assert corr["downside"] is not None and corr["downside"] > 0.999999


def test_static_baseline_uses_same_execution_engine():
    m = load_module()
    result = m.static_baseline(synthetic_panel(), cost_bps=75)
    assert result.trades
    assert all(pd.Timestamp(t["signal_date"]) < pd.Timestamp(t["date"]) for t in result.trades)
    assert all(isinstance(q, int) for rb in result.rebalances for q in rb["target_quantities"].values())


def test_independent_alpha_gate_rejects_high_correlation_and_baseline_underperformance():
    m = load_module()
    gate = m.independent_alpha_gate(
        selected_holdout={"total_return_pct": 58.0, "sharpe": 0.93, "max_drawdown_pct": -18.0},
        baseline_holdout={"total_return_pct": 68.0, "sharpe": 0.95, "max_drawdown_pct": -18.3},
        correlation={"pearson": 0.94, "downside": 0.92},
        completed_year_positive_share=0.75,
    )
    assert gate["passed"] is False
    assert "insufficient_independence_from_static_etf_sleeve" in gate["reasons"]
    assert "does_not_outperform_static_50_50_return" in gate["reasons"]
    assert "does_not_improve_static_50_50_sharpe" in gate["reasons"]


def test_independent_alpha_gate_accepts_hypothetical_strict_diversifier():
    m = load_module()
    gate = m.independent_alpha_gate(
        selected_holdout={"total_return_pct": 80.0, "sharpe": 1.10, "max_drawdown_pct": -15.0},
        baseline_holdout={"total_return_pct": 68.0, "sharpe": 0.95, "max_drawdown_pct": -18.3},
        correlation={"pearson": 0.55, "downside": 0.60},
        completed_year_positive_share=0.75,
    )
    assert gate == {"passed": True, "reasons": []}
