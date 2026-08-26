from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_executable_etf_portfolio.py"


def load_module():
    spec = importlib.util.spec_from_file_location("executable_etf", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_integer_allocation_never_uses_fractional_shares_or_overspends():
    m = load_module()
    prices = {"069500": 103_250.0, "153130": 113_360.0}
    result = m.allocate_integer_shares(
        equity=391_722.0,
        prices=prices,
        target_weights={"069500": 0.60, "153130": 0.40},
        cost_bps_per_side=25.0,
        max_position_pct=0.50,
    )

    assert all(isinstance(qty, int) and qty >= 0 for qty in result["quantities"].values())
    assert result["cash_after"] >= 0
    assert result["total_spend"] <= 391_722.0
    assert result["quantities"] == {"069500": 1, "153130": 1}
    assert result["quantities"]["069500"] * prices["069500"] / 391_722.0 <= 0.50
    assert result["quantities"]["153130"] * prices["153130"] / 391_722.0 <= 0.50


def test_allocation_handles_unaffordable_asset_without_fractional_purchase():
    m = load_module()
    result = m.allocate_integer_shares(
        equity=391_722.0,
        prices={"EXPENSIVE": 496_000.0, "CASHETF": 113_360.0},
        target_weights={"EXPENSIVE": 0.60, "CASHETF": 0.40},
        cost_bps_per_side=25.0,
        max_position_pct=0.50,
    )

    assert result["quantities"]["EXPENSIVE"] == 0
    assert result["quantities"]["CASHETF"] == 1
    assert result["cash_after"] >= 0


def test_cheaper_equity_etf_can_fit_broker_position_cap():
    m = load_module()
    prices = {"226490": 67_490.0, "153130": 113_360.0}
    result = m.allocate_integer_shares(
        equity=391_722.0,
        prices=prices,
        target_weights={"226490": 0.60, "153130": 0.40},
        cost_bps_per_side=25.0,
        max_position_pct=0.50,
    )
    assert result["quantities"] == {"226490": 2, "153130": 1}
    for code, qty in result["quantities"].items():
        assert qty * prices[code] / 391_722.0 <= 0.50


def test_rebalance_orders_are_integer_and_sell_before_buy():
    m = load_module()
    orders = m.build_rebalance_orders(
        current={"069500": 3, "153130": 0},
        target={"069500": 2, "153130": 1},
    )

    assert orders == [
        {"code": "069500", "side": "SELL", "quantity": 1},
        {"code": "153130", "side": "BUY", "quantity": 1},
    ]


def test_cost_is_charged_at_full_bps_on_each_traded_notional():
    m = load_module()
    result = m.allocate_integer_shares(
        equity=391_722.0,
        prices={"226490": 67_490.0},
        target_weights={"226490": 0.50},
        cost_bps_per_side=75.0,
        max_position_pct=0.50,
    )
    qty = result["quantities"]["226490"]
    expected = qty * 67_490.0 * (1.0 + 75.0 / 10_000.0)
    assert abs(result["total_spend"] - expected) < 1e-9
