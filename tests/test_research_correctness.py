from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, filename: str):
    script = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_drawdown_uses_explicit_equity_curve():
    m = load_script("current_live_bt_correctness", "backtest_current_live_strategy.py")
    daily_pnl = pd.Series([100.0, -50.0, -100.0], index=["d1", "d2", "d3"])

    metrics = m.calculate_portfolio_risk_metrics(daily_pnl, initial_capital=1_000.0)

    assert metrics["capital_exhausted"] is False
    assert metrics["min_equity_krw"] == 950.0
    assert metrics["max_drawdown_krw"] == -150.0
    assert abs(metrics["max_drawdown_pct"] - (-150.0 / 1_100.0 * 100.0)) < 1e-9


def test_drawdown_caps_at_minus_100_and_flags_capital_exhaustion():
    m = load_script("current_live_bt_exhaustion", "backtest_current_live_strategy.py")
    daily_pnl = pd.Series([100.0, -1_200.0], index=["d1", "d2"])

    metrics = m.calculate_portfolio_risk_metrics(daily_pnl, initial_capital=1_000.0)

    assert metrics["capital_exhausted"] is True
    assert metrics["min_equity_krw"] == -100.0
    assert metrics["max_drawdown_pct"] == -100.0
    assert metrics["raw_max_drawdown_pct_before_cap"] < -100.0


def test_naver_header_block_inference_distinguishes_q4_from_annual():
    m = load_script("naver_fundamentals_correctness", "collect_naver_quarterly_fundamentals.py")
    headers = [
        "2023.12",
        "2024.12",
        "2025.12",
        "2026.12(E)",
        "2025.03",
        "2025.06",
        "2025.09",
        "2025.12",
        "2026.03",
        "2026.06(E)",
    ]

    period_types = m.infer_period_types(headers)

    assert period_types[:4] == ["annual"] * 4
    assert period_types[4:] == ["quarterly"] * 6


def test_hml_preparation_excludes_estimates_repairs_q4_and_uses_true_yoy():
    m = load_script("hml_cma_correctness", "backtest_hml_cma_quarterly_v2.py")
    raw = pd.DataFrame(
        [
            {"code": "5930", "period": "2024.12", "year": 2024, "month": 12, "period_type": "annual", "revenue": 100.0},
            {"code": "5930", "period": "2025.03", "year": 2025, "month": 3, "period_type": "quarterly", "revenue": 20.0},
            {"code": "5930", "period": "2025.12", "year": 2025, "month": 12, "period_type": "annual", "revenue": 110.0},
            # Legacy collector mislabeled this Q4 row as annual.
            {"code": "5930", "period": "2025.12", "year": 2025, "month": 12, "period_type": "annual", "revenue": 30.0},
            {"code": "5930", "period": "2026.03", "year": 2026, "month": 3, "period_type": "quarterly", "revenue": 30.0},
            {"code": "5930", "period": "2026.12(E)", "year": 2026, "month": 12, "period_type": "annual", "revenue": 999.0},
        ]
    )

    prepared = m.prepare_fundamentals(raw)

    assert not prepared["period"].astype(str).str.contains(r"\(E\)", regex=True).any()
    q4 = prepared[(prepared["year"] == 2025) & (prepared["month"] == 12)]
    assert set(q4["period_type"]) == {"annual", "quarterly"}

    annual_2025 = q4[q4["period_type"] == "annual"].iloc[0]
    assert abs(float(annual_2025["rev_yoy"]) - 0.10) < 1e-9

    q1_2026 = prepared[(prepared["year"] == 2026) & (prepared["month"] == 3)].iloc[0]
    assert abs(float(q1_2026["rev_yoy"]) - 0.50) < 1e-9


def test_signal_delay_starts_from_actual_month_end():
    m = load_script("hml_cma_signal_date", "backtest_hml_cma_quarterly_v2.py")

    assert m.compute_signal_date(2025, 12, "quarterly") == pd.Timestamp("2026-03-01")
    assert m.compute_signal_date(2025, 12, "annual") == pd.Timestamp("2026-03-31")
