from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from toss_alpha.daily.features import compute_features


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_SOURCE = ROOT / "scripts" / "generate_contextual_daily_candidates.py"


def _load_candidate_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("generate_contextual_daily_candidates", SCRIPT_SOURCE)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module
FEATURES_SOURCE = ROOT / "src" / "toss_alpha" / "daily" / "features.py"


def test_log_return_uses_transform_not_groupby_apply_future_warning_path():
    source = FEATURES_SOURCE.read_text(encoding="utf-8")

    assert 'grouped["Close"].apply' not in source
    assert 'grouped["Close"].transform' in source


def test_log_return_matches_per_symbol_previous_close():
    panel = pd.DataFrame(
        [
            {"Date": "2026-01-01", "code": "1", "Open": 10, "High": 11, "Low": 9, "Close": 10.0, "Volume": 100},
            {"Date": "2026-01-02", "code": "1", "Open": 12, "High": 13, "Low": 11, "Close": 12.0, "Volume": 100},
            {"Date": "2026-01-01", "code": "2", "Open": 20, "High": 21, "Low": 19, "Close": 20.0, "Volume": 100},
            {"Date": "2026-01-02", "code": "2", "Open": 18, "High": 19, "Low": 17, "Close": 18.0, "Volume": 100},
        ]
    )

    features = compute_features(panel)
    by_code = features.set_index(["code", "Date"])

    assert np.isnan(by_code.loc[("000001", pd.Timestamp("2026-01-01")), "log_ret_1d"])
    assert by_code.loc[("000001", pd.Timestamp("2026-01-02")), "log_ret_1d"] == np.log(12.0 / 10.0)
    assert np.isnan(by_code.loc[("000002", pd.Timestamp("2026-01-01")), "log_ret_1d"])
    assert by_code.loc[("000002", pd.Timestamp("2026-01-02")), "log_ret_1d"] == np.log(18.0 / 20.0)


def test_candidate_dollar_volume_uses_transform_not_groupby_apply_future_warning_path():
    source = SCRIPT_SOURCE.read_text(encoding="utf-8")

    assert "g.apply(lambda x: (x[\"Close\"] * x[\"Volume\"]).shift(1))" not in source
    assert "data.groupby(\"code\")[\"raw_dollar_volume\"].shift(1)" in source


def test_candidate_dollar_volume_matches_previous_symbol_row():
    module = _load_candidate_module()
    rows = []
    for day in range(1, 26):
        date = f"2026-01-{day:02d}"
        rows.append({"Date": date, "code": "1", "Open": 10 + day, "High": 11 + day, "Low": 9 + day, "Close": 10.0 + day, "Volume": 100 + day})
        rows.append({"Date": date, "code": "2", "Open": 20 + day, "High": 21 + day, "Low": 19 + day, "Close": 20.0 + day, "Volume": 300 + day})
    panel = pd.DataFrame(rows)

    features = module.prepare_features(panel)
    by_code = features.set_index(["code", "Date"])

    assert np.isnan(by_code.loc[("1", pd.Timestamp("2026-01-01")), "dollar_volume"])
    assert by_code.loc[("1", pd.Timestamp("2026-01-02")), "dollar_volume"] == 11.0 * 101
    assert np.isnan(by_code.loc[("2", pd.Timestamp("2026-01-01")), "dollar_volume"])
    assert by_code.loc[("2", pd.Timestamp("2026-01-02")), "dollar_volume"] == 21.0 * 301


def test_whole_share_limit_sizing_converts_budget_to_integer_quantity():
    module = _load_candidate_module()

    assert module.buy_limit_price(18_790, aggressiveness_pct=0.005) == 18_890
    assert module.whole_share_quantity(55_000, 18_890) == 2
    assert module.whole_share_quantity(55_000, 73_600) == 0


def test_candidate_generation_skips_stocks_that_cannot_buy_one_whole_share():
    module = _load_candidate_module()
    rows = []
    for day in range(1, 31):
        date = f"2026-01-{day:02d}"
        rows.append({"Date": date, "code": "LOW", "name": "저가주", "Open": 9_100, "High": 9_300, "Low": 9_000, "Close": 9_160 + day, "Volume": 2_000_000})
        rows.append({"Date": date, "code": "HIGH", "name": "고가주", "Open": 73_000, "High": 74_000, "Low": 72_000, "Close": 73_200 + day, "Volume": 2_000_000})
    panel = pd.DataFrame(rows)
    policy = {
        "policy_id": "unit_test_small_account",
        "situations": {
            "flat_low_vol": {
                "mode": "momentum",
                "momentum_col": "mom_20d",
                "vol_col": "vol_20d",
                "return_col": "unit_ret",
                "min_dollar_volume": 1,
                "min_abs_momentum": 0,
                "top_n": 2,
            }
        },
        "risk_gates": {
            "max_positions": 2,
            "max_notional_krw_per_position": 55_000,
            "max_total_notional_krw": 110_000,
            "buy_limit_aggressiveness_pct": 0.005,
        },
    }

    result = module.generate(policy, panel, as_of="2026-01-30")

    assert result["sizing_model"] == "whole_share_limit_order_budget_to_quantity_with_cash_fraction_cap"
    assert all(order["quantity"] >= 1 for order in result["orders"])
    assert all(order["notional_krw"] == order["quantity"] * order["limit_price"] for order in result["orders"])
    assert all(order["notional_krw"] <= order["budget_krw"] for order in result["orders"])
    assert any(item["skip_reason"] == "cannot_buy_one_whole_share_with_budget" for item in result["skipped_orders"])


def test_candidate_generation_applies_cash_fraction_per_entry_cap():
    module = _load_candidate_module()
    rows = []
    for day in range(1, 31):
        date = f"2026-01-{day:02d}"
        rows.append({"Date": date, "code": "AAA", "name": "A", "Open": 10_000, "High": 10_500, "Low": 9_900, "Close": 10_000 + day * 20, "Volume": 2_000_000})
        rows.append({"Date": date, "code": "BBB", "name": "B", "Open": 11_000, "High": 11_500, "Low": 10_900, "Close": 11_000 + day * 30, "Volume": 2_000_000})
    panel = pd.DataFrame(rows)
    policy = {
        "policy_id": "unit_test_cash_fraction",
        "situations": {
            "up_low_vol": {
                "mode": "momentum",
                "momentum_col": "mom_20d",
                "vol_col": "vol_20d",
                "return_col": "unit_ret",
                "min_dollar_volume": 1,
                "min_abs_momentum": 0,
                "top_n": 2,
            }
        },
        "risk_gates": {
            "max_positions": 2,
            "max_notional_krw_per_position": 100_000,
            "max_total_notional_krw": 200_000,
            "cash_fraction_per_entry": 0.15,
            "portfolio_value_krw": 500_000,
            "buy_limit_aggressiveness_pct": 0.0,
        },
    }

    result = module.generate(policy, panel, as_of="2026-01-30")

    assert result["sizing_inputs"]["cash_fraction_budget_krw"] == 75_000
    assert result["sizing_inputs"]["per_position_budget_krw"] == 75_000
    assert result["orders"]
    assert all(order["budget_krw"] <= 75_000 for order in result["orders"])
    assert all(order["notional_krw"] <= 75_000 for order in result["orders"])


def test_candidate_generation_uses_max_notional_when_it_is_stricter_than_cash_fraction():
    module = _load_candidate_module()
    rows = []
    for day in range(1, 31):
        date = f"2026-01-{day:02d}"
        rows.append({"Date": date, "code": "AAA", "name": "A", "Open": 10_000, "High": 10_500, "Low": 9_900, "Close": 10_000 + day * 20, "Volume": 2_000_000})
        rows.append({"Date": date, "code": "BBB", "name": "B", "Open": 11_000, "High": 11_500, "Low": 10_900, "Close": 11_000 + day * 30, "Volume": 2_000_000})
    panel = pd.DataFrame(rows)
    policy = {
        "policy_id": "unit_test_cash_fraction_max_notional_bound",
        "situations": {
            "up_low_vol": {
                "mode": "momentum",
                "momentum_col": "mom_20d",
                "vol_col": "vol_20d",
                "return_col": "unit_ret",
                "min_dollar_volume": 1,
                "min_abs_momentum": 0,
                "top_n": 2,
            }
        },
        "risk_gates": {
            "max_positions": 2,
            "max_notional_krw_per_position": 100_000,
            "max_total_notional_krw": 200_000,
            "cash_fraction_per_entry": 0.15,
            "portfolio_value_krw": 1_000_000,
            "buy_limit_aggressiveness_pct": 0.0,
        },
    }

    result = module.generate(policy, panel, as_of="2026-01-30")

    assert result["sizing_inputs"]["cash_fraction_budget_krw"] == 150_000
    assert result["sizing_inputs"]["per_position_budget_krw"] == 100_000
    assert result["orders"]
    assert all(order["budget_krw"] <= 100_000 for order in result["orders"])
