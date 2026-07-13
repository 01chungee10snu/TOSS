from __future__ import annotations

import csv
import json
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "promote_ml_trade_condition_candidate.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("promote_ml_trade_condition_candidate", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_live_env_exports_match_best_config():
    module = _load_module()

    exports = module.live_env_exports()

    assert exports == {
        "TOSS_MAX_ORDER_KRW": "100000",
        "TOSS_MAX_POSITIONS": "3",
        "TOSS_POSITION_STOP_LOSS_PCT": "0.10",
        "TOSS_POSITION_TAKE_PROFIT_PCT": "0.08",
        "TOSS_POSITION_TRAILING_STOP_PCT": "0.05",
        "TOSS_POSITION_MAX_HOLDING_DAYS": "5",
        "TOSS_EQUITY_DRAWDOWN_STOP_PCT": "0.08",
        "TOSS_EQUITY_GUARD_COOLDOWN_DAYS": "12",
    }


def test_build_promoted_policy_updates_risk_gates_without_live_enablement(tmp_path: Path):
    module = _load_module()
    base = {
        "policy_id": "base",
        "mode": "paper_or_manual_draft_only",
        "live_trading_enabled": False,
        "risk_gates": {"max_positions": 10, "max_notional_krw_per_position": 150000, "max_total_notional_krw": 1000000},
        "situations": {"flat_low_vol": {"top_n": 3}},
    }

    policy = module.build_promoted_policy(
        base,
        search_state=tmp_path / "state.json",
        search_agg=tmp_path / "agg.csv",
        search_rows=tmp_path / "rows.csv",
        audit_path=tmp_path / "audit.json",
    )

    assert policy["policy_id"] == "ml_trade_condition_loss_averse_promoted_20260707"
    assert policy["live_trading_enabled"] is False
    assert policy["risk_gates"]["max_positions"] == 3
    assert policy["risk_gates"]["max_notional_krw_per_position"] == 100000
    assert policy["risk_gates"]["max_total_notional_krw"] == 300000
    assert policy["risk_gates"]["cash_fraction_per_entry"] == 0.15
    assert policy["risk_gates"]["assumed_initial_cash_krw"] == 1_000_000
    assert policy["live_env_exports_required"] == module.live_env_exports()
    assert policy["ml_trade_condition_promotion"]["best_config"]["take_profit_pct"] == 0.08
    assert policy["promotion_verdict"] == "PROMOTED_FOR_PAPER_OR_MANUAL_DRAFT_ONLY"


def test_csv_best_config_lookup_and_audit(tmp_path: Path):
    module = _load_module()
    state = {"status": "FINAL", "completed_configs": 2400, "row_count": 7200}
    agg = tmp_path / "agg.csv"
    rows = tmp_path / "rows.csv"

    agg_fields = [*module.BEST_CONFIG.keys(), *module.EXPECTED_SUMMARY.keys(), "promotable"]
    with agg.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=agg_fields)
        writer.writeheader()
        writer.writerow({**module.BEST_CONFIG, **module.EXPECTED_SUMMARY, "promotable": "True"})

    row_fields = [
        *module.BEST_CONFIG.keys(),
        "trade_year",
        "total_return_pct",
        "max_drawdown_pct",
        "total_trades",
        "sharpe_ratio",
    ]
    with rows.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=row_fields)
        writer.writeheader()
        for year, ret, mdd, trades, sharpe in [(2024, 25.0, -5.98, 61, 4.2193), (2025, 33.79, -3.11, 57, 7.6811), (2026, 25.46, -5.19, 35, 6.0564)]:
            writer.writerow({**module.BEST_CONFIG, "trade_year": year, "total_return_pct": ret, "max_drawdown_pct": mdd, "total_trades": trades, "sharpe_ratio": sharpe})

    best = module._load_best_agg(agg)
    year_rows = module._load_year_rows(rows)
    audit = module.build_audit(state=state, best_agg=best, year_rows=year_rows, policy_path=tmp_path / "policy.json")

    assert audit["passed"] is True
    assert audit["verdict"] == "PASS_PROMOTE_TO_PAPER_FORWARD_LOOP"
    assert [row["trade_year"] for row in year_rows] == [2024, 2025, 2026]
    assert audit["live_env_exports_required"]["TOSS_POSITION_MAX_HOLDING_DAYS"] == "5"
    assert {warning["field"] for warning in audit["parity_warnings"]} == {"cash_fraction_per_entry", "risk_cooldown_steps"}
