from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "toss_market_close_settlement.py"
    spec = importlib.util.spec_from_file_location("toss_market_close_settlement", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_historical_same_order_number_on_different_dates_is_not_overwritten(monkeypatch):
    module = _module()
    rows = [
        {
            "order_no": "0000001000",
            "broker_status": {
                "status": "FILLED",
                "filled_qty": 1,
                "order_qty": 1,
                "remaining_qty": 0,
                "raw_record": {
                    "odno": "0000001000", "ord_dt": "20260708", "pdno": "005930",
                    "sll_buy_dvsn_cd_name": "현금매수", "avg_prvs": "70000", "tot_ccld_amt": "70000",
                },
            },
        },
        {
            "order_no": "0000001000",
            "broker_status": {
                "status": "FILLED",
                "filled_qty": 1,
                "order_qty": 1,
                "remaining_qty": 0,
                "raw_record": {
                    "odno": "0000001000", "ord_dt": "20260709", "pdno": "000660",
                    "sll_buy_dvsn_cd_name": "현금매수", "avg_prvs": "150000", "tot_ccld_amt": "150000",
                },
            },
        },
    ]
    monkeypatch.setattr(module, "read_jsonl", lambda _path: rows)

    orders = module.ledger_orders_with_reconcile()

    assert [(o["order_date"], o["symbol"]) for o in orders] == [
        ("2026-07-08", "005930"),
        ("2026-07-09", "000660"),
    ]


def test_fifo_flags_sell_without_local_buy_cost():
    module = _module()
    orders = [{
        "status": "FILLED",
        "order_date": "2026-07-10",
        "symbol": "005930",
        "side": "SELL",
        "qty": 3,
        "avg": 70000,
        "amount": 210000,
    }]

    realized, stats = module.fifo_realized(orders, realized_date="2026-07-10")

    assert realized["005930"] == 0
    assert stats["005930"]["unmatched_sell_qty"] == 3


def test_period_fifo_uses_prior_buy_and_counts_only_period_unmatched():
    module = _module()
    orders = [
        {"status": "FILLED", "order_date": "2026-07-03", "symbol": "114800", "side": "BUY", "qty": 2, "avg": 1000, "amount": 2000},
        {"status": "FILLED", "order_date": "2026-07-10", "symbol": "114800", "side": "SELL", "qty": 2, "avg": 1010, "amount": 2020},
        {"status": "FILLED", "order_date": "2026-07-04", "symbol": "006400", "side": "SELL", "qty": 1, "avg": 400000, "amount": 400000},
    ]

    realized, stats = module.fifo_period(orders, period_start="2026-07-06", period_end="2026-07-10")

    assert realized["114800"] == 20
    assert stats["114800"]["sell_qty"] == 2
    assert stats.get("006400") is None


def test_sheet_summary_includes_all_tabs_and_current_week_history():
    module = _module()
    snapshot = {
        "summary": [["field", "value"], ["generated_at_utc", "2026-07-10T07:11:04+00:00"], ["symbol_count", "496"], ["approved_situations", "down_low_vol, flat_high_vol"], ["combined_test_total_return_pct", "25.77"], ["combined_test_max_drawdown_pct", "-9.71"], ["combined_test_sharpe", "1.379"], ["combined_test_total_trades", "105"]],
        "artifacts": [["category", "name", "value"], ["pipeline", "manifest_json", "/tmp/manifest.json"]],
        "history": [
            ["generated_at_utc", "period", "symbol_count", "best_variant", "approved_situations", "combined_test_total_return_pct", "combined_test_max_drawdown_pct", "combined_test_sharpe", "combined_test_total_trades", "panel_csv", "policy_json", "manifest_json"],
            ["2026-07-03T07:10:00+00:00", "p", "496", "v", "flat", "1", "-1", "1", "10", "p", "q", "m"],
            ["2026-07-06T07:10:00+00:00", "p", "496", "v", "flat", "1", "-1", "1", "10", "p", "q", "m"],
            ["2026-07-10T07:11:04+00:00", "p", "496", "v", "down_low_vol, flat_high_vol", "25.77", "-9.71", "1.379", "105", "p", "q", "m"],
        ],
    }

    result = module.summarize_sheet_snapshot(snapshot, now=datetime.fromisoformat("2026-07-10T16:25:00+09:00"))

    assert result["tabs_read"] == 3
    assert result["artifact_count"] == 1
    assert result["history_count"] == 3
    assert result["week_history_count"] == 2
    assert result["latest"]["symbol_count"] == "496"
    assert result["fresh"] is True
