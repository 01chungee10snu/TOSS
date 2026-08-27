from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backtest_hml_cma_true_pit.py"


def load_module():
    spec = importlib.util.spec_from_file_location("backtest_hml_cma_true_pit_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fundamentals() -> pd.DataFrame:
    rows = []
    for idx, code in enumerate(["000001", "000002", "000003", "000004"], start=1):
        prior_revenue = 100.0
        current_revenue = [150.0, 120.0, 90.0, 80.0][idx - 1]
        bps = [10.0, 20.0, 30.0, 40.0][idx - 1]
        common = {
            "code": code,
            "source": "opendart_receipt_xbrl",
            "is_estimate": False,
            "revision_safe": True,
            "reprt_code": "11013",
            "revenue_basis": "quarter",
            "bps": bps,
        }
        rows.append(common | {"period_end": "2024-03-31", "available_at": "2024-05-15", "revenue": prior_revenue, "rcept_no": f"20240515{idx:04d}"})
        rows.append(common | {"period_end": "2025-03-31", "available_at": "2025-05-15", "revenue": current_revenue, "rcept_no": f"20250515{idx:04d}"})
    return pd.DataFrame(rows)


def _panel() -> pd.DataFrame:
    dates = pd.bdate_range("2025-05-01", "2025-07-31")
    rows = []
    for day in dates:
        for code in ["000001", "000002", "000003", "000004"]:
            base = 100.0
            if code == "000004" and day >= pd.Timestamp("2025-06-02"):
                base = 100.0 + max(0, (day - pd.Timestamp("2025-06-02")).days) * 0.25
            rows.append(
                {
                    "date": day,
                    "code": code,
                    "Open": base,
                    "Close": base,
                    "Volume": 1_000_000,
                    "delisted": "2026-01-01" if code == "000004" else None,
                }
            )
    return pd.DataFrame(rows)


def test_factor_selection_uses_bm_and_revision_aware_revenue_yoy():
    m = load_module()
    snapshot = m.pit_factor_snapshot(_fundamentals(), "2025-05-30", universe_panel=_panel())
    closes = {code: 100.0 for code in ["000001", "000002", "000003", "000004"]}

    hml = m.select_factor_codes(snapshot, closes, strategy="hml_only", min_candidates=4, max_names=4)
    cma = m.select_factor_codes(snapshot, closes, strategy="cma_only", min_candidates=4, max_names=4)

    assert hml == ["000004"]
    assert cma == ["000004"]


def test_backtest_executes_month_end_signal_at_next_trading_day_open():
    m = load_module()
    result = m.run_backtest(
        _fundamentals(),
        _panel(),
        strategy="hml_only",
        cost_bps=31,
        min_candidates=4,
        max_names=4,
    )
    invested = [row for row in result.rebalances if row["name_count"] > 0]

    assert invested
    assert invested[0]["signal_date"] == "2025-05-30"
    assert invested[0]["execution_date"] == "2025-06-02"
    assert invested[0]["codes"] == ["000004"]
    assert result.summary()["final_equity_krw"] > 100_000_000


def test_higher_cost_stress_cannot_improve_same_path_final_equity():
    m = load_module()
    low = m.run_backtest(_fundamentals(), _panel(), strategy="hml_only", cost_bps=31, min_candidates=4, max_names=4)
    high = m.run_backtest(_fundamentals(), _panel(), strategy="hml_only", cost_bps=75, min_candidates=4, max_names=4)

    assert high.summary()["final_equity_krw"] < low.summary()["final_equity_krw"]
    assert high.summary()["total_cost_krw"] > low.summary()["total_cost_krw"]


def test_insufficient_cross_section_stays_in_cash():
    m = load_module()
    result = m.run_backtest(_fundamentals(), _panel(), strategy="hml_only", cost_bps=31, min_candidates=10, max_names=4)
    assert all(row["name_count"] == 0 for row in result.rebalances)
    assert result.summary()["final_equity_krw"] == 100_000_000
