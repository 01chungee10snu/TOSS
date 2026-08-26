from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_executable_etf_paper.py"


def load_module():
    spec = importlib.util.spec_from_file_location("executable_etf_forward_paper", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_daily_rows(now_kst_date: str, *, close: int = 10_000, volume: int = 100_000):
    now = datetime.strptime(now_kst_date, "%Y%m%d")
    rows = [
        {"stck_bsop_date": now_kst_date, "stck_clpr": str(close), "acml_vol": str(volume)}
    ]
    for i in range(1, 22):
        day = (now - timedelta(days=i)).strftime("%Y%m%d")
        rows.append({"stck_bsop_date": day, "stck_clpr": str(close), "acml_vol": str(volume)})
    return rows


def complete_market(module, *, code: str, bid: float, ask: float, avg_dtv: float = 1_000_000_000.0):
    mid = (bid + ask) / 2
    return {
        "code": code,
        "last": mid,
        "bid": bid,
        "ask": ask,
        "midpoint": mid,
        "spread_bps": (ask - bid) / mid * 10_000,
        "best_bid_quantity": 100_000,
        "best_ask_quantity": 100_000,
        "best_bid_notional_krw": bid * 100_000,
        "best_ask_notional_krw": ask * 100_000,
        "history_sessions_used": 20,
        "history_latest_date": "20260825",
        "avg_traded_value_20d_krw": avg_dtv,
        "market_data_complete": True,
    }


def test_market_evidence_collects_depth_and_20_session_average_traded_value():
    m = load_module()
    now = datetime(2026, 8, 26, 6, 0, tzinfo=timezone.utc)
    daily_rows = make_daily_rows("20260826", close=10_000, volume=100_000)

    evidence = m.market_evidence_from_payloads(
        code="226490",
        name="KODEX 코스피",
        quote_payload={"output": {"stck_prpr": "10000", "acml_vol": "123456"}},
        orderbook_payload={
            "output1": {
                "bidp1": "9990",
                "askp1": "10010",
                "bidp_rsqn1": "20000",
                "askp_rsqn1": "15000",
            }
        },
        daily_payload={"output": daily_rows},
        now=now,
    )

    assert evidence["history_sessions_used"] == 20
    assert evidence["avg_traded_value_20d_krw"] == 1_000_000_000.0
    assert evidence["best_ask_quantity"] == 15_000.0
    assert evidence["best_bid_quantity"] == 20_000.0
    assert evidence["market_data_complete"] is True
    assert evidence["history_latest_date"] != "20260826"
    assert evidence["current_session_row_present"] is True


def test_market_evidence_fails_closed_when_today_is_not_a_trading_session():
    m = load_module()
    now = datetime(2026, 8, 26, 6, 0, tzinfo=timezone.utc)
    daily_rows = make_daily_rows("20260825", close=10_000, volume=100_000)

    evidence = m.market_evidence_from_payloads(
        code="226490",
        name="KODEX 코스피",
        quote_payload={"output": {"stck_prpr": "10000", "acml_vol": "123456"}},
        orderbook_payload={
            "output1": {
                "bidp1": "9990",
                "askp1": "10010",
                "bidp_rsqn1": "20000",
                "askp_rsqn1": "15000",
            }
        },
        daily_payload={"output": daily_rows},
        now=now,
    )

    assert evidence["current_session_row_present"] is False
    assert evidence["market_data_complete"] is False


def test_order_execution_evidence_uses_side_aware_top_of_book_and_mid_slippage():
    m = load_module()
    market = complete_market(m, code="226490", bid=9_990, ask=10_010)

    buy = m.order_execution_evidence(
        {"code": "226490", "side": "BUY", "quantity": 10},
        market,
        max_order_krw=250_000,
    )
    sell = m.order_execution_evidence(
        {"code": "226490", "side": "SELL", "quantity": 10},
        market,
        max_order_krw=250_000,
    )

    assert buy["execution_price"] == 10_010
    assert sell["execution_price"] == 9_990
    assert buy["paper_slippage_bps_vs_mid"] > 0
    assert sell["paper_slippage_bps_vs_mid"] > 0
    assert buy["all_checks_passed"] is True
    assert sell["all_checks_passed"] is True


def test_order_execution_evidence_fails_closed_when_best_depth_is_too_small():
    m = load_module()
    market = complete_market(m, code="226490", bid=9_990, ask=10_010)
    market["best_ask_quantity"] = 10
    market["best_ask_notional_krw"] = 100_100

    evidence = m.order_execution_evidence(
        {"code": "226490", "side": "BUY", "quantity": 5},
        market,
        max_order_krw=250_000,
        max_top_depth_share=0.20,
    )

    assert evidence["order_best_level_depth_share"] == 0.5
    assert evidence["order_vs_best_level_depth_passed"] is False
    assert evidence["all_checks_passed"] is False


def test_forward_portfolio_completes_at_most_one_rebalance_per_month():
    m = load_module()
    validator = m.load_validator()
    markets = {
        "226490": complete_market(m, code="226490", bid=9_990, ask=10_010),
        "153130": complete_market(m, code="153130", bid=19_990, ask=20_010),
    }
    initial = {
        "schema_version": 1,
        "strategy": m.STRATEGY_ID,
        "created_at_utc": "2026-08-01T00:00:00+00:00",
        "initial_equity_krw": 1_000_000.0,
        "cash_krw": 1_000_000.0,
        "positions": {"226490": 0, "153130": 0},
        "last_rebalance_month": None,
        "completed_monthly_rebalances": 0,
        "rebalance_events": [],
        "equity_history": [],
    }

    august = datetime(2026, 8, 26, 6, 0, tzinfo=timezone.utc)
    state1, report1 = m.advance_forward_portfolio(
        state=initial,
        validator=validator,
        markets=markets,
        now=august,
        max_position_pct=0.50,
        max_order_krw=600_000,
    )
    state2, report2 = m.advance_forward_portfolio(
        state=state1,
        validator=validator,
        markets=markets,
        now=august + timedelta(days=1),
        max_position_pct=0.50,
        max_order_krw=600_000,
    )
    september = datetime(2026, 9, 1, 6, 0, tzinfo=timezone.utc)
    state3, report3 = m.advance_forward_portfolio(
        state=state2,
        validator=validator,
        markets=markets,
        now=september,
        max_position_pct=0.50,
        max_order_krw=600_000,
    )

    assert report1["completed_this_run"] is True
    assert state1["completed_monthly_rebalances"] == 1
    assert report2["rebalance_due"] is False
    assert report2["completed_this_run"] is False
    assert state2["completed_monthly_rebalances"] == 1
    assert report3["rebalance_due"] is True
    assert report3["completed_this_run"] is True
    assert state3["completed_monthly_rebalances"] == 2


def test_forward_portfolio_does_not_count_rebalance_outside_regular_market_hours():
    m = load_module()
    validator = m.load_validator()
    markets = {
        "226490": complete_market(m, code="226490", bid=9_990, ask=10_010),
        "153130": complete_market(m, code="153130", bid=19_990, ask=20_010),
    }
    initial = {
        "schema_version": 1,
        "strategy": m.STRATEGY_ID,
        "created_at_utc": "2026-08-01T00:00:00+00:00",
        "initial_equity_krw": 1_000_000.0,
        "cash_krw": 1_000_000.0,
        "positions": {"226490": 0, "153130": 0},
        "last_rebalance_month": None,
        "completed_monthly_rebalances": 0,
        "rebalance_events": [],
        "equity_history": [],
    }

    state, report = m.advance_forward_portfolio(
        state=initial,
        validator=validator,
        markets=markets,
        now=datetime(2026, 8, 26, 13, 0, tzinfo=timezone.utc),  # 22:00 KST
        max_position_pct=0.50,
        max_order_krw=600_000,
    )

    assert report["market_session_open"] is False
    assert report["completed_this_run"] is False
    assert state["completed_monthly_rebalances"] == 0
    assert state["positions"] == initial["positions"]


def test_forward_portfolio_does_not_mutate_positions_when_liquidity_gate_fails():
    m = load_module()
    validator = m.load_validator()
    bad_market = complete_market(m, code="226490", bid=9_990, ask=10_010)
    bad_market["best_ask_quantity"] = 1
    markets = {
        "226490": bad_market,
        "153130": complete_market(m, code="153130", bid=19_990, ask=20_010),
    }
    initial = {
        "schema_version": 1,
        "strategy": m.STRATEGY_ID,
        "created_at_utc": "2026-08-01T00:00:00+00:00",
        "initial_equity_krw": 1_000_000.0,
        "cash_krw": 1_000_000.0,
        "positions": {"226490": 0, "153130": 0},
        "last_rebalance_month": None,
        "completed_monthly_rebalances": 0,
        "rebalance_events": [],
        "equity_history": [],
    }

    state, report = m.advance_forward_portfolio(
        state=initial,
        validator=validator,
        markets=markets,
        now=datetime(2026, 8, 26, 6, 0, tzinfo=timezone.utc),
        max_position_pct=0.50,
        max_order_krw=600_000,
    )

    assert report["liquidity_gate"]["liquidity_gate_passed"] is False
    assert report["completed_this_run"] is False
    assert state["positions"] == initial["positions"]
    assert state["cash_krw"] == initial["cash_krw"]
    assert state["completed_monthly_rebalances"] == 0
