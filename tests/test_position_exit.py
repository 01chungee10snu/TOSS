from __future__ import annotations

import json as _json
import time as _time
from datetime import datetime, timezone
from types import SimpleNamespace

from toss_alpha.data.schema import AccountSnapshot, PositionSnapshot, Quote
from toss_alpha.execution.position_exit import (
    append_position_exit_orders,
    block_buys_for_position_quote_errors,
    build_position_exit_orders,
    evaluate_account_equity_guard,
    merge_exit_orders,
    position_quote_invalid_reason,
    position_exit_market_regime,
    position_exit_sell_symbols,
)


def test_build_position_exit_orders_generates_sell_on_stop_loss_with_sellable_quantity():
    positions = [
        PositionSnapshot(
            symbol="307930",
            quantity=9,
            sellable_quantity=9,
            avg_price=6000,
            market_value=9 * 5500,
            unrealized_pnl=-4500,
            source="kis",
        )
    ]

    orders, audit = build_position_exit_orders(positions, env={"TOSS_POSITION_STOP_LOSS_PCT": "0.06"}, as_of="2026-07-06")

    assert audit["positions_checked"] == 1
    assert audit["sell_order_count"] == 1
    order = orders[0]
    assert order["symbol"] == "307930"
    assert order["side"] == "SELL"
    assert order["quantity"] == 9
    assert order["limit_price"] == 5500
    assert order["current_price"] == 5500.0
    assert order["quote_source"] == "position_market_value_derived"
    assert order["reason"] == "stop_loss_6.00%"
    assert order["position_snapshot"]["as_of"] == positions[0].as_of.isoformat()


def test_live_position_exit_uses_fresh_quote_last_for_signal_and_bid_for_limit():
    position = PositionSnapshot(
        symbol="307930", quantity=9, sellable_quantity=9, avg_price=6000,
        market_value=9 * 6100, source="kis",
    )
    quote = Quote(
        symbol="307930", timestamp=datetime.now(timezone.utc), last=5500,
        bid=5495, ask=5500, volume=1_000_000, source="kis",
    )

    orders, audit = build_position_exit_orders(
        [position],
        env={"TOSS_POSITION_STOP_LOSS_PCT": "0.06"},
        realtime_quotes={"307930": quote},
        require_realtime_quotes=True,
    )

    assert audit["sell_order_count"] == 1
    assert orders[0]["current_price"] == 5500
    assert orders[0]["limit_price"] == 5495
    assert orders[0]["quote_source"] == "kis"
    assert orders[0]["reason"] == "stop_loss_6.00%"


def test_live_position_exit_blocks_forced_sell_when_fresh_quote_missing():
    position = PositionSnapshot(
        symbol="307930", quantity=9, sellable_quantity=9, avg_price=6000,
        market_value=9 * 5500, source="kis",
    )

    orders, audit = build_position_exit_orders(
        [position], env={"TOSS_FORCE_EXIT_ALL": "1"},
        realtime_quotes={}, require_realtime_quotes=True,
    )

    assert orders == []
    assert audit["reviews"][0]["action"] == "BLOCKED"
    assert "fresh_exit_quote_missing" in audit["reviews"][0]["blocked_reasons"]


def test_position_quote_errors_remove_buys_but_preserve_sells():
    payload = {
        "status": "CANDIDATES",
        "orders": [
            {"symbol": "005930", "side": "BUY", "quantity": 1},
            {"symbol": "000660", "side": "SELL", "quantity": 1},
        ],
    }

    blocked = block_buys_for_position_quote_errors(payload, {"307930": "RuntimeError:quote unavailable"})

    assert [(order["symbol"], order["side"]) for order in blocked["orders"]] == [("000660", "SELL")]
    assert blocked["buy_gate_blocked"] is True
    assert blocked["buy_gate_reason"] == "position_exit_quote_unavailable"


def test_position_quote_validator_rejects_empty_or_incomplete_kis_quotes():
    now = datetime.now(timezone.utc)
    assert position_quote_invalid_reason(Quote(symbol="307930", timestamp=now, last=0, bid=None, ask=None, volume=None, source="kis")) == "invalid_position_quote:last_nonpositive"
    assert position_quote_invalid_reason(Quote(symbol="307930", timestamp=now, last=5500, bid=None, ask=5510, volume=1_000_000, source="kis")) == "invalid_position_quote:bid_missing"
    assert position_quote_invalid_reason(Quote(symbol="307930", timestamp=now, last=5500, bid=5495, ask=5510, volume=1_000_000, source="kis")) is None


def test_append_position_exit_incomplete_quote_blocks_buys_and_preserves_sells(tmp_path, monkeypatch):
    from toss_alpha.execution import position_exit as module

    config = SimpleNamespace(
        provider="kis", app_key="app", app_secret="secret", cano="12345678",
        account_product_code="01", kis_mock_trading=False,
        base_url="https://example.invalid", timeout=1,
    )
    monkeypatch.setattr(module.LiveExecutionConfig, "from_env", staticmethod(lambda _env: config))

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def position_snapshots(self):
            return [PositionSnapshot(symbol="307930", quantity=9, sellable_quantity=9, avg_price=6000, market_value=49_500, source="kis")]

        def account_snapshot(self):
            return AccountSnapshot(account_id="demo", total_equity=100_000, cash=50_000, source="kis")

        def quote_snapshot(self, symbol):
            return Quote(symbol=symbol, timestamp=datetime.now(timezone.utc), last=0, bid=None, ask=None, volume=None, source="kis")

    monkeypatch.setattr(module, "KisReadOnlyClient", FakeClient)
    payload = {
        "status": "CANDIDATES",
        "orders": [
            {"symbol": "005930", "side": "BUY", "quantity": 1},
            {"symbol": "000660", "side": "SELL", "quantity": 1},
        ],
    }

    merged, audit = append_position_exit_orders(payload, report_dir=tmp_path, env={"TOSS_POSITION_EXIT_ENABLED": "true"})

    assert [(order["symbol"], order["side"]) for order in merged["orders"]] == [("000660", "SELL")]
    assert audit["block_new_buys"] is True
    assert audit["buy_block_reasons"] == ["position_exit_quote_unavailable"]
    assert audit["position_quote_errors"]["307930"] == "invalid_position_quote:last_nonpositive"
    assert audit["position_quote_count"] == 0


def test_append_position_exit_quote_failure_blocks_existing_buys_and_preserves_sells(tmp_path, monkeypatch):
    from toss_alpha.execution import position_exit as module

    config = SimpleNamespace(
        provider="kis", app_key="app", app_secret="secret", cano="12345678",
        account_product_code="01", kis_mock_trading=False,
        base_url="https://example.invalid", timeout=1,
    )
    monkeypatch.setattr(module.LiveExecutionConfig, "from_env", staticmethod(lambda _env: config))

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def position_snapshots(self):
            return [PositionSnapshot(symbol="307930", quantity=9, sellable_quantity=9, avg_price=6000, market_value=49_500, source="kis")]

        def account_snapshot(self):
            return AccountSnapshot(account_id="demo", total_equity=100_000, cash=50_000, source="kis")

        def quote_snapshot(self, _symbol):
            raise RuntimeError("quote unavailable")

    monkeypatch.setattr(module, "KisReadOnlyClient", FakeClient)
    payload = {
        "status": "CANDIDATES",
        "orders": [
            {"symbol": "005930", "side": "BUY", "quantity": 1},
            {"symbol": "000660", "side": "SELL", "quantity": 1},
        ],
    }

    merged, audit = append_position_exit_orders(payload, report_dir=tmp_path, env={"TOSS_POSITION_EXIT_ENABLED": "true"})

    assert [(order["symbol"], order["side"]) for order in merged["orders"]] == [("000660", "SELL")]
    assert audit["block_new_buys"] is True
    assert audit["buy_block_reasons"] == ["position_exit_quote_unavailable"]
    assert "307930" in audit["position_quote_errors"]


def test_build_position_exit_orders_blocks_when_sellable_missing():
    positions = [PositionSnapshot(symbol="307930", quantity=9, avg_price=6000, market_value=9 * 5500)]

    orders, audit = build_position_exit_orders(positions)

    assert orders == []
    assert audit["reviews"][0]["action"] == "BLOCKED"
    assert "sellable_quantity_missing_or_zero" in audit["reviews"][0]["blocked_reasons"]


def test_build_position_exit_orders_force_all_for_equity_drawdown_stop():
    positions = [
        PositionSnapshot(
            symbol="307930",
            quantity=9,
            sellable_quantity=9,
            avg_price=6000,
            market_value=9 * 6040,
            source="kis",
        )
    ]

    orders, audit = build_position_exit_orders(positions, env={"TOSS_FORCE_EXIT_ALL": "1"})

    assert audit["force_all"] is True
    assert audit["sell_order_count"] == 1
    assert orders[0]["reason"] == "equity_drawdown_stop"
    assert orders[0]["quantity"] == 9


def test_account_equity_guard_triggers_and_persists_wall_clock_cooldown(tmp_path):
    """Trigger sets an absolute wall-clock cooldown, not a step counter."""
    state_path = tmp_path / "live_equity_guard_state.json"
    state_path.write_text('{"peak_equity": 1000000}', encoding="utf-8")
    positions = [PositionSnapshot(symbol="307930", quantity=9, sellable_quantity=9, market_value=54000)]
    account = AccountSnapshot(account_id="demo", total_equity=930000, cash=876000, source="kis")

    audit = evaluate_account_equity_guard(
        account, positions, report_dir=tmp_path,
        env={"TOSS_EQUITY_DRAWDOWN_STOP_PCT": "0.06", "TOSS_EQUITY_GUARD_COOLDOWN_SECONDS": "3600"},
    )

    assert audit["status"] == "TRIGGERED"
    assert audit["block_new_buys"] is True
    assert audit["liquidation_required"] is True
    assert audit["cooldown_seconds"] == 3600
    assert audit["cooldown_remaining_seconds"] >= 3500  # ~1h remaining
    saved = _json.loads(state_path.read_text(encoding="utf-8"))
    assert "cooldown_until_ts" in saved
    assert "cooldown_remaining" not in saved  # old step field gone
    assert state_path.exists()


def test_account_equity_guard_blocks_buys_during_active_wall_clock_cooldown(tmp_path):
    """If cooldown_until_ts is in the future, BUYs are blocked without a new trigger."""
    state_path = tmp_path / "live_equity_guard_state.json"
    future_ts = _time.time() + 7200  # 2 hours from now
    state_path.write_text(_json.dumps({"peak_equity": 1000000, "cooldown_until_ts": future_ts}), encoding="utf-8")
    account = AccountSnapshot(account_id="demo", total_equity=990000, cash=990000, source="kis")

    audit = evaluate_account_equity_guard(account, [], report_dir=tmp_path, env={"TOSS_EQUITY_DRAWDOWN_STOP_PCT": "0.06"})

    assert audit["status"] == "COOLDOWN"
    assert audit["block_new_buys"] is True
    assert audit["liquidation_required"] is False
    assert audit["cooldown_remaining_seconds"] >= 7100


def test_account_equity_guard_clears_cooldown_after_expiry(tmp_path):
    """An expired cooldown_until_ts returns to READY and clears block."""
    state_path = tmp_path / "live_equity_guard_state.json"
    state_path.write_text(_json.dumps({"peak_equity": 1000000, "cooldown_until_ts": 1.0}), encoding="utf-8")  # epoch 1 = 1970
    account = AccountSnapshot(account_id="demo", total_equity=990000, cash=990000, source="kis")

    audit = evaluate_account_equity_guard(account, [], report_dir=tmp_path, env={"TOSS_EQUITY_DRAWDOWN_STOP_PCT": "0.06"})

    assert audit["status"] == "READY"
    assert audit["block_new_buys"] is False
    assert audit["cooldown_active"] is False


def test_equity_guard_blocks_non_finite_equity(tmp_path):
    for value in (float("nan"), float("inf"), float("-inf")):
        account = AccountSnapshot(account_id="demo", total_equity=value, cash=0, source="kis")
        audit = evaluate_account_equity_guard(account, [], report_dir=tmp_path)
        assert audit["status"] == "BLOCKED_MISSING_EQUITY"
        assert audit["block_new_buys"] is True


def test_equity_guard_fails_closed_on_corrupt_state(tmp_path):
    (tmp_path / "live_equity_guard_state.json").write_text("{broken", encoding="utf-8")
    account = AccountSnapshot(account_id="demo", total_equity=900000, cash=900000, source="kis")

    audit = evaluate_account_equity_guard(account, [], report_dir=tmp_path)

    assert audit["status"] == "BLOCKED_CORRUPT_GUARD_STATE"
    assert audit["block_new_buys"] is True


def test_merge_exit_orders_gives_sell_priority_over_same_symbol_buy():
    candidate_payload = {
        "status": "CANDIDATES",
        "orders": [
            {"symbol": "307930", "side": "BUY", "quantity": 1},
            {"symbol": "005930", "side": "BUY", "quantity": 1},
        ],
    }
    sell_orders = [{"symbol": "307930", "side": "SELL", "quantity": 9, "sellable_quantity": 9, "limit_price": 5500}]

    merged = merge_exit_orders(candidate_payload, sell_orders)

    assert [order["side"] for order in merged["orders"]] == ["SELL", "BUY"]
    assert [order["symbol"] for order in merged["orders"]] == ["307930", "005930"]
    assert merged["position_exit_applied"] is True


def test_daily_or_inverse_strategy_label_cannot_authorize_regime_liquidation():
    payload = {
        "situation": "inverse_sleeve_risk_off",
        "source_situation": "down_high_vol",
        "strategy_type": "inverse_sleeve",
    }

    assert position_exit_market_regime(payload) is None


def test_only_fresh_non_conflicting_intraday_envelope_authorizes_regime_liquidation():
    payload = {
        "situation": "inverse_sleeve_risk_off",
        "intraday_decision": {
            "evidence_status": "FRESH",
            "signal_conflict": False,
            "regime_liquidation_allowed": True,
            "market_regime": "down_high_vol",
        },
    }

    assert position_exit_market_regime(payload) == "down_high_vol"
    payload["intraday_decision"]["signal_conflict"] = True
    assert position_exit_market_regime(payload) is None


def test_intraday_sell_authorization_is_symbol_specific_and_fail_closed():
    payload = {
        "intraday_decision": {
            "verdict": "SELL",
            "evidence_status": "FRESH",
            "signal_conflict": False,
            "sell_symbols": ["6400", "006400"],
        }
    }

    assert position_exit_sell_symbols(payload) == ["006400"]
    payload["intraday_decision"]["evidence_status"] = "STALE"
    assert position_exit_sell_symbols(payload) == []


# ── Trailing stop tests ────────────────────────────────────────────────

def test_trailing_stop_triggers_when_price_drops_from_peak(tmp_path):
    """Position rose above entry then fell back: trailing stop should fire."""
    tracker_path = tmp_path / "live_position_tracker.json"
    # Position bought at 6000, peaked at 6500, now at 6100.
    # Trailing 5%: 6500 * 0.95 = 6175 → 6100 < 6175 → trigger.
    tracker_path.write_text(_json.dumps({"307930": {"peak_price": 6500.0, "first_seen_date": "2026-07-01"}}), encoding="utf-8")
    positions = [
        PositionSnapshot(symbol="307930", quantity=9, sellable_quantity=9, avg_price=6000, market_value=9 * 6100, source="kis"),
    ]

    orders, audit = build_position_exit_orders(
        positions,
        env={"TOSS_POSITION_TRAILING_STOP_PCT": "0.05"},
        report_dir=tmp_path,
    )

    assert audit["sell_order_count"] == 1
    assert any("trailing_stop" in r for r in orders[0]["reason"].split(","))


def test_trailing_stop_does_not_trigger_when_not_in_profit(tmp_path):
    """If peak < avg_price (never profitable), trailing stop stays inactive."""
    tracker_path = tmp_path / "live_position_tracker.json"
    tracker_path.write_text(_json.dumps({"307930": {"peak_price": 5900.0, "first_seen_date": "2026-07-01"}}), encoding="utf-8")
    positions = [
        PositionSnapshot(symbol="307930", quantity=9, sellable_quantity=9, avg_price=6000, market_value=9 * 5800, source="kis"),
    ]

    orders, audit = build_position_exit_orders(
        positions,
        env={"TOSS_POSITION_TRAILING_STOP_PCT": "0.05", "TOSS_POSITION_STOP_LOSS_PCT": "0.10"},
        report_dir=tmp_path,
    )

    # Trailing stop should NOT fire (peak 5900 < avg 6000).
    # But stop_loss 10% also shouldn't fire (5800 > 5400). So HOLD.
    assert audit["sell_order_count"] == 0
    assert audit["reviews"][0]["action"] == "HOLD"


def test_trailing_stop_updates_peak_price_in_tracker(tmp_path):
    """When current price exceeds stored peak, tracker should be updated."""
    tracker_path = tmp_path / "live_position_tracker.json"
    tracker_path.write_text(_json.dumps({"307930": {"peak_price": 6200.0, "first_seen_date": "2026-07-01"}}), encoding="utf-8")
    positions = [
        PositionSnapshot(symbol="307930", quantity=9, sellable_quantity=9, avg_price=6000, market_value=9 * 6500, source="kis"),
    ]

    build_position_exit_orders(
        positions,
        env={"TOSS_POSITION_TRAILING_STOP_PCT": "0.05"},
        report_dir=tmp_path,
    )

    saved = _json.loads(tracker_path.read_text(encoding="utf-8"))
    assert saved["307930"]["peak_price"] == 6500.0


# ── Time exit tests ────────────────────────────────────────────────────

def test_time_exit_triggers_after_max_holding_days(tmp_path):
    """Position held longer than max_holding_days triggers time_exit."""
    from datetime import date as _date, timedelta
    tracker_path = tmp_path / "live_position_tracker.json"
    old_date = (_date.today() - timedelta(days=40)).isoformat()
    tracker_path.write_text(_json.dumps({"307930": {"peak_price": 6100.0, "first_seen_date": old_date}}), encoding="utf-8")
    positions = [
        PositionSnapshot(symbol="307930", quantity=9, sellable_quantity=9, avg_price=6000, market_value=9 * 6050, source="kis"),
    ]

    orders, audit = build_position_exit_orders(
        positions,
        env={"TOSS_POSITION_MAX_HOLDING_DAYS": "20"},
        report_dir=tmp_path,
    )

    assert audit["sell_order_count"] == 1
    assert any("time_exit" in r for r in orders[0]["reason"].split(","))


def test_time_exit_does_not_trigger_within_holding_period(tmp_path):
    """Position held for fewer days than max should not trigger time_exit."""
    tracker_path = tmp_path / "live_position_tracker.json"
    tracker_path.write_text(_json.dumps({"307930": {"peak_price": 6100.0, "first_seen_date": "2026-07-04"}}), encoding="utf-8")
    positions = [
        PositionSnapshot(symbol="307930", quantity=9, sellable_quantity=9, avg_price=6000, market_value=9 * 6050, source="kis"),
    ]

    orders, audit = build_position_exit_orders(
        positions,
        env={"TOSS_POSITION_MAX_HOLDING_DAYS": "20", "TOSS_POSITION_STOP_LOSS_PCT": "0.10"},
        report_dir=tmp_path,
    )

    assert audit["sell_order_count"] == 0


# ── Regime risk_off exit test ──────────────────────────────────────────

def test_regime_risk_off_triggers_exit(tmp_path):
    """When market_regime is risk_off and risk_off_exit is enabled, all positions exit."""
    positions = [
        PositionSnapshot(symbol="307930", quantity=9, sellable_quantity=9, avg_price=6000, market_value=9 * 6050, source="kis"),
    ]

    orders, audit = build_position_exit_orders(
        positions,
        env={"TOSS_POSITION_RISK_OFF_EXIT": "true"},
        market_regime="risk_off",
    )

    assert audit["sell_order_count"] == 1
    assert "regime_risk_off" in orders[0]["reason"]


def test_down_high_vol_is_treated_as_risk_off_exit_regime(tmp_path):
    positions = [
        PositionSnapshot(symbol="307930", quantity=9, sellable_quantity=9, avg_price=6000, market_value=9 * 6050, source="kis"),
    ]

    orders, audit = build_position_exit_orders(
        positions,
        env={"TOSS_POSITION_RISK_OFF_EXIT": "true"},
        market_regime="down_high_vol",
    )

    assert audit["sell_order_count"] == 1
    assert "regime_risk_off" in orders[0]["reason"]


def test_regime_risk_off_does_not_trigger_in_normal_market(tmp_path):
    """When market_regime is not risk_off, no regime exit."""
    positions = [
        PositionSnapshot(symbol="307930", quantity=9, sellable_quantity=9, avg_price=6000, market_value=9 * 6050, source="kis"),
    ]

    orders, audit = build_position_exit_orders(
        positions,
        env={"TOSS_POSITION_RISK_OFF_EXIT": "true", "TOSS_POSITION_STOP_LOSS_PCT": "0.10"},
        market_regime="risk_on",
    )

    assert audit["sell_order_count"] == 0


def test_inverse_hedge_is_held_while_risk_off_persists():
    positions = [
        PositionSnapshot(
            symbol="114800",
            quantity=47,
            sellable_quantity=47,
            avg_price=1009,
            market_value=47 * 1010,
            source="kis",
        ),
    ]

    orders, audit = build_position_exit_orders(
        positions,
        env={
            "TOSS_POSITION_RISK_OFF_EXIT": "true",
            "TOSS_POSITION_STOP_LOSS_PCT": "0.10",
            "TOSS_POSITION_TAKE_PROFIT_PCT": "0.10",
        },
        market_regime="inverse_sleeve_risk_off",
    )

    assert orders == []
    assert audit["reviews"][0]["action"] == "HOLD"
    assert "regime_risk_off" not in audit["reviews"][0]["reasons"]


def test_inverse_hedge_exits_when_risk_off_clears():
    positions = [
        PositionSnapshot(
            symbol="114800",
            quantity=47,
            sellable_quantity=47,
            avg_price=1009,
            market_value=47 * 1010,
            source="kis",
        ),
    ]

    orders, audit = build_position_exit_orders(
        positions,
        env={
            "TOSS_POSITION_RISK_OFF_EXIT": "true",
            "TOSS_POSITION_STOP_LOSS_PCT": "0.10",
            "TOSS_POSITION_TAKE_PROFIT_PCT": "0.10",
        },
        market_regime="risk_on",
    )

    assert audit["sell_order_count"] == 1
    assert orders[0]["symbol"] == "114800"
    assert "inverse_regime_recovery" in orders[0]["reason"]


def test_inverse_hedge_stop_loss_still_exits_during_risk_off():
    positions = [
        PositionSnapshot(
            symbol="114800",
            quantity=47,
            sellable_quantity=47,
            avg_price=1000,
            market_value=47 * 900,
            source="kis",
        ),
    ]

    orders, audit = build_position_exit_orders(
        positions,
        env={
            "TOSS_POSITION_RISK_OFF_EXIT": "true",
            "TOSS_POSITION_STOP_LOSS_PCT": "0.05",
            "TOSS_POSITION_TAKE_PROFIT_PCT": "0.10",
        },
        market_regime="risk_off",
    )

    assert audit["sell_order_count"] == 1
    assert "stop_loss_5.00%" in orders[0]["reason"]
    assert "regime_risk_off" not in orders[0]["reason"]


# ── max_positions enforcement test ─────────────────────────────────────

def test_enforce_max_positions_trims_excess_buys():
    from toss_alpha.execution.position_exit import _enforce_max_positions

    merged = {
        "status": "CANDIDATES",
        "orders": [
            {"symbol": "307930", "side": "SELL", "quantity": 9},
            {"symbol": "005930", "side": "BUY", "quantity": 1},
            {"symbol": "000660", "side": "BUY", "quantity": 1},
            {"symbol": "035420", "side": "BUY", "quantity": 1},
        ],
    }

    # A submitted SELL does not free a slot until broker reconciliation proves
    # it filled. held=3, max=4 therefore leaves exactly one BUY slot.
    result = _enforce_max_positions(merged, held_count=3, max_positions=4)

    buys = [o for o in result["orders"] if o["side"] == "BUY"]
    sells = [o for o in result["orders"] if o["side"] == "SELL"]
    assert len(sells) == 1  # SELL always kept
    assert len(buys) == 1
    assert result["max_positions_trimmed_buys"] == 2


def test_enforce_max_positions_allows_all_when_under_limit():
    from toss_alpha.execution.position_exit import _enforce_max_positions

    merged = {
        "status": "CANDIDATES",
        "orders": [
            {"symbol": "005930", "side": "BUY", "quantity": 1},
            {"symbol": "000660", "side": "BUY", "quantity": 1},
        ],
    }

    # held=1, max=4, no SELLs → effective_held=1, slots=3 → both BUYs kept.
    result = _enforce_max_positions(merged, held_count=1, max_positions=4)

    buys = [o for o in result["orders"] if o["side"] == "BUY"]
    assert len(buys) == 2
    assert "max_positions_trimmed_buys" not in result


def test_repurchase_resets_stale_peak_and_holding_age(tmp_path):
    tracker = tmp_path / "live_position_tracker.json"
    tracker.write_text(_json.dumps({
        "005930": {
            "peak_price": 150.0,
            "first_seen_date": "2026-01-01",
            "avg_price": 90.0,
            "quantity": 10.0,
        }
    }), encoding="utf-8")
    positions = [PositionSnapshot(
        symbol="005930", quantity=10, sellable_quantity=10,
        avg_price=100, market_value=1100, source="kis",
    )]

    orders, audit = build_position_exit_orders(
        positions,
        env={"TOSS_POSITION_TRAILING_STOP_PCT": "0.05", "TOSS_POSITION_STOP_LOSS_PCT": "0.20"},
        report_dir=tmp_path,
    )

    assert orders == []
    assert audit["reviews"][0]["peak_price"] == 110.0
    assert audit["reviews"][0]["held_trading_days"] == 0


def test_tracker_prunes_closed_symbols_when_tracking_rules_disabled(tmp_path):
    tracker = tmp_path / "live_position_tracker.json"
    tracker.write_text(_json.dumps({
        "005930": {"peak_price": 150.0, "first_seen_date": "2026-01-01"}
    }), encoding="utf-8")

    build_position_exit_orders([], env={}, report_dir=tmp_path)

    assert _json.loads(tracker.read_text(encoding="utf-8")) == {}


def _inverse_quote(price: float, *, session_high: float | None = None) -> Quote:
    return Quote(
        symbol="114800", timestamp=datetime.now(timezone.utc), last=price,
        bid=price - 1, ask=price, volume=1_000_000, session_high=session_high, source="kis",
    )


def test_inverse_first_profit_stage_sells_33_percent_at_four_percent_gain(tmp_path):
    position = PositionSnapshot(
        symbol="114800", quantity=300, sellable_quantity=300,
        avg_price=1000, market_value=312_000, source="kis",
    )
    orders, audit = build_position_exit_orders(
        [position], report_dir=tmp_path,
        realtime_quotes={"114800": _inverse_quote(1040)}, require_realtime_quotes=True,
    )

    assert len(orders) == 1
    assert orders[0]["quantity"] == 99
    assert orders[0]["exit_stage"] == "inverse_profit_1"
    assert orders[0]["idempotency_scope"].startswith("inverse_profit_1-")
    assert audit["reviews"][0]["protected_price"] == 1024.4


def test_inverse_second_profit_stage_waits_for_broker_quantity_reduction(tmp_path):
    tracker = tmp_path / "live_position_tracker.json"
    tracker.write_text(_json.dumps({"114800": {
        "avg_price": 1000, "quantity": 300, "initial_quantity": 300,
        "peak_price": 1040, "first_seen_date": "2026-07-14", "lifecycle_id": "life1",
    }}), encoding="utf-8")
    position = PositionSnapshot(
        symbol="114800", quantity=201, sellable_quantity=201,
        avg_price=1000, market_value=209_040, source="kis",
    )
    orders, _ = build_position_exit_orders(
        [position], report_dir=tmp_path,
        realtime_quotes={"114800": _inverse_quote(1040)}, require_realtime_quotes=True,
    )

    assert len(orders) == 1
    assert orders[0]["quantity"] == 99
    assert orders[0]["exit_stage"] == "inverse_profit_2"


def test_inverse_profit_lock_exits_remainder_before_gain_turns_negative(tmp_path):
    tracker = tmp_path / "live_position_tracker.json"
    tracker.write_text(_json.dumps({"114800": {
        "avg_price": 1000, "quantity": 201, "initial_quantity": 300,
        "peak_price": 1040, "first_seen_date": "2026-07-14", "lifecycle_id": "life1",
    }}), encoding="utf-8")
    position = PositionSnapshot(
        symbol="114800", quantity=201, sellable_quantity=201,
        avg_price=1000, market_value=201 * 1024, source="kis",
    )
    orders, audit = build_position_exit_orders(
        [position], report_dir=tmp_path,
        realtime_quotes={"114800": _inverse_quote(1024)}, require_realtime_quotes=True,
    )

    assert orders[0]["quantity"] == 201
    assert orders[0]["exit_stage"] == "full_exit"
    assert "inverse_profit_lock" in orders[0]["reason"]
    assert audit["reviews"][0]["protected_price"] == 1024.4


def test_inverse_restart_repairs_peak_from_official_session_high(tmp_path):
    tracker = tmp_path / "live_position_tracker.json"
    tracker.write_text(_json.dumps({"114800": {
        "avg_price": 1000, "quantity": 300, "initial_quantity": 300,
        "peak_price": 1010, "first_seen_date": "2026-07-14", "lifecycle_id": "life1",
    }}), encoding="utf-8")
    position = PositionSnapshot(
        symbol="114800", quantity=300, sellable_quantity=300,
        avg_price=1000, market_value=300 * 1020, source="kis",
    )
    orders, audit = build_position_exit_orders(
        [position], report_dir=tmp_path,
        realtime_quotes={"114800": _inverse_quote(1020, session_high=1040)}, require_realtime_quotes=True,
    )

    assert orders[0]["quantity"] == 300
    assert "inverse_profit_lock" in orders[0]["reason"]
    assert audit["reviews"][0]["peak_price"] == 1040
