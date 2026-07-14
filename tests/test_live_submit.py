from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import requests
import pytest

from toss_alpha.execution.krx_calendar import (
    is_krx_trading_day,
    next_krx_trading_day,
    previous_krx_trading_day,
    week_first_krx_trading_day,
    week_last_krx_trading_day,
)
from toss_alpha.execution.live_submit import (
    LiveOrderLedger,
    adapt_buy_order_to_live_quote,
    aggregate_sell_quantity_violation,
    current_issue_buy_violation,
    fill_probability_violation,
    intraday_decision_buy_violation,
    korea_regular_market_violation,
    live_data_freshness_violations,
    market_regime_violation,
    order_quality_violations,
    order_to_intent,
    promoted_policy_guard_violation,
    recent_candidate_violation,
    run_live_submit_phase,
    sellable_quantity_violation,
    strategic_harness_audit_buy_violation,
    validate_live_order_intent,
)
from toss_alpha.risk import RiskPolicy
from toss_alpha.execution.live_ready import LiveExecutionConfig
from toss_alpha.data.schema import Quote


def _candidate_payload():
    return {
        "status": "CANDIDATES",
        "as_of": "2025-12-30",
        "orders": [
            {
                "symbol": "319400",
                "name": "현대무벡스",
                "side": "BUY",
                "order_type": "LIMIT",
                "quantity": 2,
                "limit_price": 18890,
                "notional_krw": 37780,
                "mode": "manual_draft_only",
                "reason": "approved_situation=up_low_vol",
            }
        ],
    }


def _aggressive_candidate_payload():
    return {
        **_candidate_payload(),
        "as_of": "2026-07-03",
        "situation": "down_high_vol",
        "policy_id": "contextual_mon_fri_policy_seed20260607_aggressive_small_account",
    }


def _write_promoted_candidate(path: Path, *, status: str = "NO_TRADE") -> Path:
    path.write_text(
        '{"as_of":"2026-07-03","status":"' + status + '","reason":"situation_not_approved:down_high_vol","orders":[]}',
        encoding="utf-8",
    )
    return path


def _live_ready():
    return {"status": "LIVE_READY", "ready": True, "missing": [], "dry_run_available": True}


def _qual_ready():
    return {"status": "READY", "reasons": []}


def _env(tmp_path: Path):
    return {
        "BROKER_PROVIDER": "kis",
        "KIS_APP_KEY": "app",
        "KIS_APP_SECRET": "sec",
        "KIS_CANO": "12345678",
        "KIS_ACNT_PRDT_CD": "01",
        "KIS_LIVE_TRADING_ENABLED": "true",
        "TOSS_RISK_LIVE_TRADING_ENABLED": "true",
        "TOSS_MAX_ORDER_KRW": "55000",
        "TOSS_LIVE_ORDER_LEDGER": str(tmp_path / "ledger.jsonl"),
    }


def test_adaptive_buy_uses_best_ask_and_reduces_quantity():
    class FakeQuoteClient:
        def quote_snapshot(self, symbol):
            return Quote(symbol=symbol, timestamp=datetime.now(timezone.utc), last=1040, bid=1040, ask=1041, volume=1_000_000, source="kis")

    order, audit = adapt_buy_order_to_live_quote(
        {"symbol": "114800", "side": "BUY", "limit_price": 1023, "current_price": 1023, "quantity": 48, "notional_krw": 49104},
        config=LiveExecutionConfig.from_env({"BROKER_PROVIDER": "kis", "KIS_APP_KEY": "app", "KIS_APP_SECRET": "sec", "KIS_CANO": "12345678"}),
        env={"TOSS_ADAPTIVE_LIMIT_MAX_CHASE_PCT": "0.02", "TOSS_MAX_LIVE_SPREAD_PCT": "0.003"},
        quote_client=FakeQuoteClient(),
    )

    assert audit["status"] == "ADAPTED"
    assert order["limit_price"] == 1041
    assert order["quantity"] == 47
    assert order["notional_krw"] <= 49104


def test_adaptive_buy_blocks_above_chase_cap():
    class FakeQuoteClient:
        def quote_snapshot(self, symbol):
            return Quote(symbol=symbol, timestamp=datetime.now(timezone.utc), last=1080, bid=1079, ask=1080, volume=1_000_000, source="kis")

    original = {"symbol": "114800", "side": "BUY", "limit_price": 1023, "current_price": 1023, "quantity": 48, "notional_krw": 49104}
    order, audit = adapt_buy_order_to_live_quote(
        original,
        config=LiveExecutionConfig.from_env({"BROKER_PROVIDER": "kis", "KIS_APP_KEY": "app", "KIS_APP_SECRET": "sec", "KIS_CANO": "12345678"}),
        env={"TOSS_ADAPTIVE_LIMIT_MAX_CHASE_PCT": "0.02"},
        quote_client=FakeQuoteClient(),
    )

    assert audit["violation"] == "adaptive_limit_chase_cap_exceeded"
    assert order["limit_price"] == 1023


def test_adaptive_buy_uses_reference_close_not_aggressive_generated_limit():
    class FakeQuoteClient:
        def quote_snapshot(self, symbol):
            return Quote(symbol=symbol, timestamp=datetime.now(timezone.utc), last=14_000, bid=13_990, ask=14_000, volume=1_000_000, source="kis")

    _, audit = adapt_buy_order_to_live_quote(
        {"symbol": "005930", "side": "BUY", "limit_price": 15_000, "reference_close": 10_000, "quantity": 3, "notional_krw": 45_000},
        config=LiveExecutionConfig.from_env({"BROKER_PROVIDER": "kis", "KIS_APP_KEY": "app", "KIS_APP_SECRET": "sec", "KIS_CANO": "12345678"}),
        env={"TOSS_ADAPTIVE_LIMIT_MAX_CHASE_PCT": "0.02"},
        quote_client=FakeQuoteClient(),
    )

    assert audit["reference_price"] == 10_000
    assert audit["chase_pct"] == pytest.approx(0.4)
    assert audit["violation"] == "adaptive_limit_chase_cap_exceeded"


def test_adaptive_buy_blocks_when_bid_or_ask_missing():
    class FakeQuoteClient:
        def quote_snapshot(self, symbol):
            return Quote(symbol=symbol, timestamp=datetime.now(timezone.utc), last=10_000, bid=None, ask=None, volume=1_000_000, source="kis")

    _, audit = adapt_buy_order_to_live_quote(
        {"symbol": "005930", "side": "BUY", "current_price": 10_000, "limit_price": 10_010, "quantity": 5, "notional_krw": 50_000},
        config=LiveExecutionConfig.from_env({"BROKER_PROVIDER": "kis", "KIS_APP_KEY": "app", "KIS_APP_SECRET": "sec", "KIS_CANO": "12345678"}),
        env={},
        quote_client=FakeQuoteClient(),
    )

    assert audit["violation"] == "adaptive_quote_orderbook_missing"


def test_adaptive_buy_requires_explicit_unadjusted_reference_price():
    class FakeQuoteClient:
        def quote_snapshot(self, symbol):
            raise AssertionError("missing reference must block before quote fetch")

    _, audit = adapt_buy_order_to_live_quote(
        {"symbol": "005930", "side": "BUY", "limit_price": 15_000, "quantity": 3, "notional_krw": 45_000},
        config=LiveExecutionConfig.from_env({"BROKER_PROVIDER": "kis", "KIS_APP_KEY": "app", "KIS_APP_SECRET": "sec", "KIS_CANO": "12345678"}),
        env={},
        quote_client=FakeQuoteClient(),
    )

    assert audit["violation"] == "adaptive_quote_prerequisite_missing"


def test_live_submit_dry_run_builds_kis_payload_without_http(tmp_path, monkeypatch):
    def fail_post(*_args, **_kwargs):
        raise AssertionError("dry-run must not call HTTP")

    monkeypatch.setattr(requests, "post", fail_post)
    result = run_live_submit_phase(
        candidate_payload=_candidate_payload(),
        qual=_qual_ready(),
        live=_live_ready(),
        report_dir=tmp_path,
        env=_env(tmp_path),
        now=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )

    assert result["status"] == "LIVE_SUBMIT_DRY_RUN_READY"
    assert result["submitted_count"] == 0
    assert result["results"][0]["status"] == "DRY_RUN"
    assert result["results"][0]["payload"] == {
        "CANO": "12345678",
        "ACNT_PRDT_CD": "01",
        "PDNO": "319400",
        "ORD_DVSN": "00",
        "ORD_QTY": "2",
        "ORD_UNPR": "18890",
        "EXCG_ID_DVSN_CD": "KRX",
        "SLL_TYPE": "",
        "CNDT_PRIC": "",
    }


def test_real_no_order_tick_still_reconciles_active_ledger(tmp_path, monkeypatch):
    from toss_alpha.execution import live_submit as ls

    calls = []

    def fake_reconcile(*, ledger_path, env, desired_order_keys, now):
        calls.append((ledger_path, desired_order_keys, now))
        return {"status": "OK", "checked_count": 1, "updated_count": 1}

    monkeypatch.setattr(ls, "manage_submitted_order_ledger", fake_reconcile)
    env = {**_env(tmp_path), "TOSS_LIVE_SUBMIT_DRY_RUN": "false"}

    result = run_live_submit_phase(
        candidate_payload={"status": "NO_TRADE", "as_of": "2026-07-10", "orders": []},
        qual=_qual_ready(),
        live=_live_ready(),
        report_dir=tmp_path,
        env=env,
        now=datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "LIVE_SUBMIT_NO_ORDERS"
    assert result["order_reconcile"]["checked_count"] == 1
    assert len(calls) == 1
    assert calls[0][1] == set()


def test_live_submit_blocks_when_qual_data_is_blocked(tmp_path):
    result = run_live_submit_phase(
        candidate_payload=_candidate_payload(),
        qual={"status": "BLOCKED_QUAL_DATA", "reasons": ["missing_opendart_api_key"]},
        live=_live_ready(),
        report_dir=tmp_path,
        env=_env(tmp_path),
        now=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )

    assert result["status"] == "LIVE_SUBMIT_DRY_RUN_BLOCKED"
    assert "qual_gate_blocked" in result["violations"]
    assert result["results"][0]["status"] == "BLOCK"


def test_real_submit_requires_submit_opt_in_and_confirmation(tmp_path):
    env = {**_env(tmp_path), "TOSS_LIVE_SUBMIT_DRY_RUN": "false"}
    result = run_live_submit_phase(
        candidate_payload=_candidate_payload(),
        qual=_qual_ready(),
        live=_live_ready(),
        report_dir=tmp_path,
        env=env,
        now=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )

    assert result["status"] == "LIVE_SUBMIT_BLOCKED"
    assert "live_submit_not_enabled" in result["violations"]
    assert "real_order_confirmation_phrase_mismatch" in result["violations"]


def test_duplicate_submitted_ledger_key_blocks_next_attempt(tmp_path):
    env = _env(tmp_path)
    intent = order_to_intent(_candidate_payload()["orders"][0], strategy_id="ttak_absolute_return_loop")
    ledger = LiveOrderLedger(Path(env["TOSS_LIVE_ORDER_LEDGER"]))
    ledger.append({"ledger_key": "2025-12-30:ttak_absolute_return_loop:319400:BUY", "status": "SUBMITTED"})

    result = run_live_submit_phase(
        candidate_payload=_candidate_payload(),
        qual=_qual_ready(),
        live=_live_ready(),
        report_dir=tmp_path,
        env=env,
        now=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )

    assert intent.symbol == "319400"
    assert result["status"] == "LIVE_SUBMIT_DRY_RUN_BLOCKED"
    assert "duplicate_live_order_ledger_key" in result["results"][0]["violations"]


def test_live_order_ledger_reservation_is_atomic_for_same_key(tmp_path):
    ledger = LiveOrderLedger(tmp_path / "ledger.jsonl")
    key = "2026-07-10:ttak_absolute_return_loop:005930:BUY"
    row = {"ledger_key": key, "status": "PENDING_SUBMIT", "timestamp": "2026-07-10T01:00:00+00:00"}

    assert ledger.reserve_if_absent(key, row) is True
    assert ledger.reserve_if_absent(key, row) is False
    assert ledger.has_live_submission(key) is True


def test_ambiguous_broker_timeout_is_recorded_unknown_and_blocks_retry(tmp_path, monkeypatch):
    def raise_timeout(self, intent, decision, *, confirmation_phrase, dry_run):
        raise requests.Timeout("response lost")

    monkeypatch.setattr("toss_alpha.execution.live_submit.GuardedLiveExecutor.submit_manual_draft", raise_timeout)
    panel = tmp_path / "panel.csv"
    panel.write_text("Date,symbol,Close\n2026-07-10,005930,70000\n", encoding="utf-8")
    sell_payload = {
        "status": "CANDIDATES",
        "as_of": "2026-07-10",
        "situation": "flat_low_vol",
        "orders": [{
            "symbol": "005930", "side": "SELL", "order_type": "LIMIT",
            "quantity": 1, "sellable_quantity": 1, "limit_price": 70000,
            "current_price": 70000, "notional_krw": 70000,
            "mode": "live_auto_guarded", "reason": "stop_loss",
        }],
    }
    env = {
        **_env(tmp_path),
        "TOSS_LIVE_SUBMIT_DRY_RUN": "false",
        "TOSS_LIVE_SUBMIT_ENABLED": "true",
        "TOSS_LIVE_SUBMIT_CONFIRMATION": "I UNDERSTAND THIS IS A REAL ORDER",
        "TOSS_MAX_ORDER_KRW": "150000",
        "TOSS_ORDER_RECONCILE_ENABLED": "false",
        "TOSS_PANEL_CSV": str(panel),
    }

    result = run_live_submit_phase(
        candidate_payload=sell_payload, qual=_qual_ready(), live=_live_ready(),
        report_dir=tmp_path, env=env,
        now=datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc),
    )

    key = "2026-07-10:ttak_absolute_return_loop:005930:SELL"
    assert result["status"] == "LIVE_SUBMIT_OUTCOME_UNKNOWN"
    assert result["unknown_count"] == 1
    assert LiveOrderLedger(Path(env["TOSS_LIVE_ORDER_LEDGER"])).has_live_submission(key) is True


def test_real_submit_before_0900_kst_is_blocked_without_http(tmp_path, monkeypatch):
    def fail_post(*_args, **_kwargs):
        raise AssertionError("pre-open real submit must not call HTTP")

    monkeypatch.setattr(requests, "post", fail_post)
    env = {
        **_env(tmp_path),
        "TOSS_LIVE_SUBMIT_ENABLED": "true",
        "TOSS_LIVE_SUBMIT_DRY_RUN": "false",
        "TOSS_LIVE_SUBMIT_CONFIRMATION": "I UNDERSTAND THIS IS A REAL ORDER",
    }
    result = run_live_submit_phase(
        candidate_payload=_candidate_payload(),
        qual=_qual_ready(),
        live=_live_ready(),
        report_dir=tmp_path,
        env=env,
        now=datetime(2026, 7, 2, 23, 10, tzinfo=timezone.utc),  # 2026-07-03 08:10 KST
    )

    assert result["status"] == "LIVE_SUBMIT_BLOCKED"
    assert "before_korea_regular_market_open_0900_kst" in result["violations"]
    assert result["results"][0]["status"] == "BLOCK"


def test_real_submit_blocks_stale_candidate_after_open_without_http(tmp_path, monkeypatch):
    def fail_post(*_args, **_kwargs):
        raise AssertionError("stale real submit must not call HTTP")

    monkeypatch.setattr(requests, "post", fail_post)
    env = {
        **_env(tmp_path),
        "TOSS_LIVE_SUBMIT_ENABLED": "true",
        "TOSS_LIVE_SUBMIT_DRY_RUN": "false",
        "TOSS_LIVE_SUBMIT_CONFIRMATION": "I UNDERSTAND THIS IS A REAL ORDER",
    }
    result = run_live_submit_phase(
        candidate_payload=_candidate_payload(),
        qual=_qual_ready(),
        live=_live_ready(),
        report_dir=tmp_path,
        env=env,
        now=datetime(2026, 7, 3, 1, 0, tzinfo=timezone.utc),  # 2026-07-03 10:00 KST
    )

    assert result["status"] == "LIVE_SUBMIT_BLOCKED"
    assert "candidate_as_of_stale" in result["violations"]
    assert result["results"][0]["status"] == "BLOCK"


def test_korea_regular_market_violation_window():
    assert korea_regular_market_violation(datetime(2026, 7, 2, 23, 59, tzinfo=timezone.utc)) == "before_korea_regular_market_open_0900_kst"
    assert korea_regular_market_violation(datetime(2026, 7, 3, 0, 0, tzinfo=timezone.utc)) is None
    assert korea_regular_market_violation(datetime(2026, 7, 3, 6, 21, tzinfo=timezone.utc)) == "after_korea_regular_market_last_buy_1520_kst"


def test_korea_regular_market_violation_blocks_known_krx_holiday_and_env_override():
    assert korea_regular_market_violation(datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc)) == "outside_krx_trading_day"
    assert korea_regular_market_violation(
        datetime(2026, 7, 3, 1, 0, tzinfo=timezone.utc),
        env={"TOSS_KRX_HOLIDAYS": "2026-07-03"},
    ) == "outside_krx_trading_day"


def test_real_submit_blocks_krx_holiday_without_http(tmp_path, monkeypatch):
    def fail_post(*_args, **_kwargs):
        raise AssertionError("holiday real submit must not call HTTP")

    monkeypatch.setattr(requests, "post", fail_post)
    env = {
        **_env(tmp_path),
        "TOSS_LIVE_SUBMIT_ENABLED": "true",
        "TOSS_LIVE_SUBMIT_DRY_RUN": "false",
        "TOSS_LIVE_SUBMIT_CONFIRMATION": "I UNDERSTAND THIS IS A REAL ORDER",
        "TOSS_KRX_HOLIDAYS": "2026-07-03",
    }
    result = run_live_submit_phase(
        candidate_payload={**_candidate_payload(), "as_of": "2026-07-03"},
        qual=_qual_ready(),
        live=_live_ready(),
        report_dir=tmp_path,
        env=env,
        now=datetime(2026, 7, 3, 1, 0, tzinfo=timezone.utc),  # 2026-07-03 10:00 KST
    )

    assert result["status"] == "LIVE_SUBMIT_BLOCKED"
    assert "outside_krx_trading_day" in result["violations"]
    assert result["results"][0]["status"] == "BLOCK"


def test_promoted_no_trade_blocks_aggressive_live_submit_without_http(tmp_path, monkeypatch):
    def fail_post(*_args, **_kwargs):
        raise AssertionError("promoted NO_TRADE aggressive block must not call HTTP")

    monkeypatch.setattr(requests, "post", fail_post)
    promoted_json = _write_promoted_candidate(tmp_path / "promoted.json", status="NO_TRADE")
    env = {
        **_env(tmp_path),
        "TOSS_LIVE_SUBMIT_ENABLED": "true",
        "TOSS_LIVE_SUBMIT_DRY_RUN": "false",
        "TOSS_LIVE_SUBMIT_CONFIRMATION": "I UNDERSTAND THIS IS A REAL ORDER",
        "TOSS_PROMOTED_CANDIDATE_JSON": str(promoted_json),
    }
    result = run_live_submit_phase(
        candidate_payload=_aggressive_candidate_payload(),
        qual=_qual_ready(),
        live=_live_ready(),
        report_dir=tmp_path,
        env=env,
        now=datetime(2026, 7, 3, 1, 0, tzinfo=timezone.utc),  # 2026-07-03 10:00 KST
    )

    assert result["status"] == "LIVE_SUBMIT_BLOCKED"
    assert "promoted_policy_no_trade_blocks_aggressive_live" in result["violations"]
    assert result["results"][0]["status"] == "BLOCK"


def test_promoted_policy_guard_allows_non_aggressive_and_promoted_candidates(tmp_path):
    promoted_json = _write_promoted_candidate(tmp_path / "promoted.json", status="CANDIDATES")
    env = {"TOSS_PROMOTED_CANDIDATE_JSON": str(promoted_json)}

    assert promoted_policy_guard_violation(_candidate_payload(), root=tmp_path, env=env) is None
    assert promoted_policy_guard_violation(_aggressive_candidate_payload(), root=tmp_path, env=env) is None


def test_regime_recent_fill_liquidity_and_bad_event_guards():
    assert market_regime_violation({"situation": "down_high_vol"}) == "market_regime_blocked:down_high_vol"
    assert market_regime_violation({"situation": "flat_low_vol"}) is None
    assert recent_candidate_violation({"as_of": "2026-07-01"}, now=datetime(2026, 7, 3, 1, 0, tzinfo=timezone.utc)) == "candidate_as_of_not_recent_krx_trading_day"
    assert fill_probability_violation({"side": "BUY", "limit_price": 1000, "current_price": 1010}) == "fill_probability_low_limit_too_far_below_current"
    assert order_quality_violations(
        {
            "symbol": "005930",
            "side": "BUY",
            "limit_price": 1000,
            "current_price": 1001,
            "dollar_volume": 2_000_000_000,
            "spread_pct": 0.001,
            "risk_tags": ["capital_raise"],
        },
        {},
    ) == ["bad_event_veto:capital_raise"]


def test_live_data_freshness_blocks_stale_panel_and_sentiment(tmp_path):
    panel = tmp_path / "panel.csv"
    panel.write_text("Date,code,Open,High,Low,Close,Volume\n2026-06-01,005930,1,1,1,1,1\n", encoding="utf-8")
    sentiment = tmp_path / "sentiment.json"
    sentiment.write_text('{"latest_panel_date":"2026-06-01"}', encoding="utf-8")
    violations = live_data_freshness_violations(
        root=tmp_path,
        now=datetime(2026, 7, 3, 1, 0, tzinfo=timezone.utc),
        env={"TOSS_PANEL_CSV": str(panel), "TOSS_SENTIMENT_REPORT_JSON": str(sentiment)},
    )

    assert "panel_latest_date_stale" in violations
    assert "sentiment_latest_panel_date_stale" in violations


def test_krx_calendar_helpers_handle_weekend_holiday_and_week_boundaries():
    assert is_krx_trading_day(date(2026, 7, 3)) is True
    assert is_krx_trading_day(date(2026, 7, 4)) is False
    assert is_krx_trading_day(date(2026, 10, 5)) is False
    assert next_krx_trading_day(date(2026, 10, 2)) == date(2026, 10, 6)
    assert previous_krx_trading_day(date(2026, 10, 6)) == date(2026, 10, 2)
    assert week_first_krx_trading_day(date(2026, 10, 7)) == date(2026, 10, 6)
    assert week_last_krx_trading_day(date(2026, 10, 7)) == date(2026, 10, 8)


def test_validate_live_order_intent_rejects_market_and_fractional_quantity():
    intent = order_to_intent(
        {"symbol": "005930", "side": "BUY", "order_type": "MARKET", "quantity": 0.5, "notional_krw": 50_000},
        strategy_id="s",
    )
    violations = validate_live_order_intent(intent, raw_order={}, policy=RiskPolicy(live_trading_enabled=True, max_order_krw=55_000))

    assert "only_limit_orders_allowed" in violations
    assert "quantity_must_be_positive_integer" in violations
    assert "limit_price_required" in violations


def test_sell_order_requires_explicit_sellable_quantity_and_blocks_shortfall():
    intent = order_to_intent(
        {"symbol": "005930", "side": "SELL", "order_type": "LIMIT", "quantity": 4, "limit_price": 70_000, "notional_krw": 280_000},
        strategy_id="s",
    )

    assert sellable_quantity_violation(intent, raw_order={}) == "sellable_quantity_missing"
    assert sellable_quantity_violation(intent, raw_order={"sellable_quantity": 3}) == "sellable_quantity_shortfall"
    assert sellable_quantity_violation(intent, raw_order={"sellable_quantity": 4}) is None
    assert sellable_quantity_violation(intent, raw_order={"sellable_quantity": "nan", "ord_psbl_qty": 4}) is None
    assert sellable_quantity_violation(intent, raw_order={"sellable_quantity": "nan"}) == "sellable_quantity_missing"


def test_risk_reducing_sell_can_exceed_entry_notional_cap_when_fully_sellable():
    raw = {
        "symbol": "005930", "side": "SELL", "order_type": "LIMIT",
        "quantity": 4, "sellable_quantity": 4, "limit_price": 70_000,
        "notional_krw": 280_000,
    }
    intent = order_to_intent(raw, strategy_id="s")

    violations = validate_live_order_intent(
        intent, raw_order=raw,
        policy=RiskPolicy(live_trading_enabled=True, max_order_krw=150_000),
    )

    assert "max_order_krw_exceeded" not in violations
    assert sellable_quantity_violation(intent, raw_order=raw) is None


def test_aggregate_sell_quantity_blocks_duplicate_symbol_over_sell():
    orders = [
        {"symbol": "005930", "side": "SELL", "quantity": 6, "sellable_quantity": 10},
        {"symbol": "005930", "side": "SELL", "quantity": 5, "sellable_quantity": 10},
        {"symbol": "000660", "side": "SELL", "quantity": 1, "sellable_quantity": 1},
    ]

    assert aggregate_sell_quantity_violation(orders[0], orders) == "aggregate_sell_quantity_shortfall"
    assert aggregate_sell_quantity_violation(orders[2], orders) is None


def test_live_submit_sell_dry_run_allowed_when_sellable_quantity_covers_order(tmp_path, monkeypatch):
    def fail_post(*_args, **_kwargs):
        raise AssertionError("sell dry-run must not call HTTP")

    monkeypatch.setattr(requests, "post", fail_post)
    sell_payload = {
        "status": "CANDIDATES",
        "as_of": "2026-07-03",
        "orders": [
            {
                "symbol": "307930",
                "name": "컴퍼니케이",
                "side": "SELL",
                "order_type": "LIMIT",
                "quantity": 9,
                "sellable_quantity": 9,
                "limit_price": 6040,
                "notional_krw": 54360,
                "mode": "manual_draft_only",
                "reason": "position_exit_review",
            }
        ],
    }

    result = run_live_submit_phase(
        candidate_payload=sell_payload,
        qual=_qual_ready(),
        live=_live_ready(),
        report_dir=tmp_path,
        env=_env(tmp_path),
        now=datetime(2026, 7, 3, 1, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "LIVE_SUBMIT_DRY_RUN_READY"
    assert result["results"][0]["status"] == "DRY_RUN"
    assert result["results"][0]["payload"]["PDNO"] == "307930"
    assert result["results"][0]["payload"]["ORD_QTY"] == "9"


def test_real_sell_exit_bypasses_buy_only_regime_and_quality_blocks(tmp_path, monkeypatch):
    captured = {}

    def fake_submit(self, intent, decision, *, confirmation_phrase, dry_run):
        captured["decision"] = decision
        return {
            "status": "SUBMITTED" if decision.allow else "BLOCK",
            "violations": decision.violations,
            "provider": "kis",
            "symbol": intent.symbol,
            "side": intent.side,
            "json": {"rt_cd": "0", "output": {"ODNO": "0000000001", "KRX_FWDG_ORD_ORGNO": "03420"}},
        }

    monkeypatch.setattr("toss_alpha.execution.live_submit.GuardedLiveExecutor.submit_manual_draft", fake_submit)
    sell_payload = {
        "status": "CANDIDATES",
        "as_of": "2026-07-01",
        # Missing situation and stale candidate/data create BUY-only phase gates.
        "orders": [
            {
                "symbol": "001510",
                "name": "SK증권",
                "side": "SELL",
                "order_type": "LIMIT",
                "quantity": 40,
                "sellable_quantity": 40,
                "limit_price": 2370,
                "notional_krw": 94800,
                "mode": "live_auto_guarded",
                "reason": "manual_rebound_exit_watchdog",
            }
        ],
    }
    panel = tmp_path / "panel.csv"
    panel.write_text("Date,symbol,Close\n2026-07-01,001510,2390\n", encoding="utf-8")
    promoted_json = _write_promoted_candidate(tmp_path / "promoted.json", status="NO_TRADE")
    env = {
        **_env(tmp_path),
        "TOSS_LIVE_SUBMIT_DRY_RUN": "false",
        "TOSS_LIVE_SUBMIT_ENABLED": "true",
        "TOSS_LIVE_SUBMIT_CONFIRMATION": "I UNDERSTAND THIS IS A REAL ORDER",
        "TOSS_MAX_ORDER_KRW": "150000",
        "TOSS_ORDER_RECONCILE_ENABLED": "false",
        "TOSS_PANEL_CSV": str(panel),
        "TOSS_PROMOTED_CANDIDATE_JSON": str(promoted_json),
    }

    result = run_live_submit_phase(
        candidate_payload=sell_payload,
        qual={"status": "BLOCKED_QUAL_DATA", "reasons": ["unrelated_buy_event"]},
        live=_live_ready(),
        report_dir=tmp_path,
        env=env,
        now=datetime(2026, 7, 8, 1, 30, tzinfo=timezone.utc),
    )

    assert result["status"] == "LIVE_SUBMITTED"
    assert result["submitted_count"] == 1
    assert captured["decision"].allow is True
    assert "market_regime_missing" not in captured["decision"].violations
    assert "promoted_policy_no_trade_blocks_aggressive_live" not in captured["decision"].violations
    assert "qual_gate_blocked" not in captured["decision"].violations
    assert "candidate_as_of_not_recent_krx_trading_day" not in captured["decision"].violations
    assert "panel_latest_date_stale" not in captured["decision"].violations
    assert "liquidity_quality_missing_dollar_volume" not in captured["decision"].violations


def test_intraday_decision_requires_fresh_matching_buy_verdict():
    now = datetime(2026, 7, 10, 1, 30, tzinfo=timezone.utc)
    ordinary = {"symbol": "006400", "side": "BUY"}
    inverse = {"symbol": "114800", "side": "BUY"}
    payload = {
        "intraday_decision": {
            "decision_id": "intraday-test123",
            "verdict": "LONG_BUY",
            "evidence_status": "FRESH",
            "news_evidence_status": "FRESH",
            "signal_conflict": False,
            "generated_at_utc": now.isoformat(),
        }
    }

    assert intraday_decision_buy_violation(ordinary, payload, now=now, env={}) is None
    assert intraday_decision_buy_violation(inverse, payload, now=now, env={}) == "intraday_verdict_mismatch:LONG_BUY:INVERSE_BUY"
    payload["intraday_decision"]["signal_conflict"] = True
    assert intraday_decision_buy_violation(ordinary, payload, now=now, env={}) == "intraday_signal_conflict"
    assert intraday_decision_buy_violation(ordinary, {}, now=now, env={}) == "intraday_decision_missing"


def test_current_issue_report_blocks_buy_but_not_sell(tmp_path):
    issue_dir = tmp_path / "reports" / "harness" / "current_issues"
    issue_dir.mkdir(parents=True)
    report = issue_dir / "current_issue_risk_report_20260708.json"
    report.write_text('{"as_of":"2026-07-08","severity":"critical","buy_gate":"block_new_buy"}', encoding="utf-8")
    now = datetime(2026, 7, 8, 0, 30, tzinfo=timezone.utc)

    assert current_issue_buy_violation({"symbol": "005930", "side": "BUY"}, root=tmp_path, now=now, env={}) == "current_issue_buy_block:critical"
    assert current_issue_buy_violation({"symbol": "005930", "side": "SELL"}, root=tmp_path, now=now, env={}) is None
    assert current_issue_buy_violation({"symbol": "005930", "side": "BUY"}, root=tmp_path, now=now, env={"TOSS_ALLOW_CURRENT_ISSUE_BUY": "true"}) is None


def test_current_issue_report_missing_is_fail_closed_for_buy(tmp_path):
    now = datetime(2026, 7, 8, 0, 30, tzinfo=timezone.utc)
    assert current_issue_buy_violation({"symbol": "005930", "side": "BUY"}, root=tmp_path, now=now, env={}) == "current_issue_report_missing"
    assert current_issue_buy_violation({"symbol": "005930", "side": "BUY"}, root=tmp_path, now=now, env={"TOSS_REQUIRE_CURRENT_ISSUE_REPORT": "false"}) is None


def test_strategic_harness_audit_is_required_and_must_be_fresh_pass(tmp_path):
    path = tmp_path / "reports" / "harness" / "strategic_live_decision_harness_audit.json"
    path.parent.mkdir(parents=True)
    now = datetime(2026, 7, 8, 0, 30, tzinfo=timezone.utc)
    buy = {"symbol": "005930", "side": "BUY"}

    assert strategic_harness_audit_buy_violation(buy, root=tmp_path, now=now, env={}) == "strategic_harness_audit_missing"
    path.write_text('{"status":"FAIL","generated_at_utc":"2026-07-08T00:29:00+00:00"}', encoding="utf-8")
    assert strategic_harness_audit_buy_violation(buy, root=tmp_path, now=now, env={}) == "strategic_harness_audit_not_pass"
    path.write_text('{"status":"PASS","generated_at_utc":"2026-07-08T00:29:00+00:00"}', encoding="utf-8")
    assert strategic_harness_audit_buy_violation(buy, root=tmp_path, now=now, env={}) is None
    assert strategic_harness_audit_buy_violation({"side": "SELL"}, root=tmp_path, now=now, env={}) is None


def test_current_issue_report_blocks_inverse_etf_without_explicit_global_override(tmp_path):
    issue_dir = tmp_path / "reports" / "harness" / "current_issues"
    issue_dir.mkdir(parents=True)
    report = issue_dir / "current_issue_risk_report_20260708.json"
    report.write_text('{"as_of":"2026-07-08","severity":"critical","buy_gate":"block_new_buy"}', encoding="utf-8")
    now = datetime(2026, 7, 8, 0, 30, tzinfo=timezone.utc)

    inverse = {"symbol": "252670", "side": "BUY", "reason": "inverse_sleeve:risk_off_bad_regime"}
    assert current_issue_buy_violation(inverse, root=tmp_path, now=now, env={}) == "current_issue_buy_block:critical"
    assert current_issue_buy_violation(inverse, root=tmp_path, now=now, env={"TOSS_ALLOW_CURRENT_ISSUE_BUY": "true"}) is None
