from __future__ import annotations

from datetime import datetime, timedelta, timezone

from toss_alpha.execution.live_submit import intraday_decision_buy_violation, market_regime_violation
from toss_alpha.execution.symbol_issue import apply_symbol_issue_gate, apply_symbol_market_overlay, evaluate_symbol_issues

NOW = datetime(2026, 7, 16, 5, 0, tzinfo=timezone.utc)


def candidate():
    return {
        "status": "CANDIDATES",
        "situation": "down_high_vol",
        "orders": [
            {"symbol": "111111", "name": "호재기업", "side": "BUY", "quantity": 20, "limit_price": 1000, "notional_krw": 20000},
            {"symbol": "222222", "name": "무소식기업", "side": "BUY", "quantity": 10, "limit_price": 2000, "notional_krw": 20000},
        ],
    }


def decision(*, market_day=-0.01, market_regime="risk_off", severity="low", evidence="FRESH"):
    return {
        "decision_id": "intraday-test",
        "generated_at_utc": NOW.isoformat(),
        "verdict": "HOLD",
        "evidence_status": evidence,
        "news_evidence_status": "FRESH",
        "news_severity": severity,
        "signal_conflict": False,
        "market_regime": market_regime,
        "metrics": {"market_day_return": market_day},
    }


def test_symbol_issue_requires_fresh_positive_company_specific_event():
    events = [
        {"symbol": "111111", "name": "호재기업", "title": "호재기업, 300억원 공급계약 체결", "reported_at": NOW.isoformat()},
        {"symbol": "222222", "name": "무소식기업", "title": "다른기업 신제품 출시", "reported_at": NOW.isoformat()},
    ]
    audit = evaluate_symbol_issues(candidate()["orders"], events, now=NOW)
    assert audit["verdicts_by_symbol"]["111111"] == "BUY"
    assert audit["verdicts_by_symbol"]["222222"] == "WATCH"


def test_symbol_issue_negative_or_review_event_overrides_positive():
    events = [
        {"symbol": "111111", "name": "호재기업", "title": "호재기업 공급계약 체결", "reported_at": NOW.isoformat()},
        {"symbol": "111111", "name": "호재기업", "title": "호재기업 횡령 의혹", "reported_at": NOW.isoformat()},
        {"symbol": "222222", "name": "무소식기업", "title": "무소식기업 유상증자 결정", "reported_at": NOW.isoformat()},
    ]
    audit = evaluate_symbol_issues(candidate()["orders"], events, now=NOW)
    assert audit["verdicts_by_symbol"]["111111"] == "VETO"
    assert audit["verdicts_by_symbol"]["222222"] == "REVIEW"


def test_stale_positive_event_is_watch_not_buy():
    events = [{"symbol": "111111", "name": "호재기업", "title": "호재기업 대규모 수주", "reported_at": (NOW - timedelta(hours=13)).isoformat()}]
    audit = evaluate_symbol_issues(candidate()["orders"], events, now=NOW, max_age_seconds=12 * 3600)
    assert audit["verdicts_by_symbol"]["111111"] == "WATCH"


def test_opendart_symbol_binding_allows_report_title_without_company_name():
    events = [{"symbol": "111111", "title": "단일판매ㆍ공급계약체결", "reported_at": (NOW - timedelta(hours=20)).isoformat(), "source": "opendart"}]
    audit = evaluate_symbol_issues(candidate()["orders"], events, now=NOW)
    assert audit["verdicts_by_symbol"]["111111"] == "BUY"


def test_issue_gate_keeps_only_buy_symbols_and_preserves_sell():
    payload = candidate()
    payload["orders"].append({"symbol": "333333", "side": "SELL", "quantity": 1})
    audit = {"verdicts_by_symbol": {"111111": "BUY", "222222": "WATCH"}, "symbols": {}}
    effective = apply_symbol_issue_gate(payload, audit, require_positive=True)
    assert [(o["symbol"], o["side"]) for o in effective["orders"]] == [("111111", "BUY"), ("333333", "SELL")]
    assert effective["orders"][0]["symbol_issue_verdict"] == "BUY"


def test_risk_off_market_scales_company_buy_instead_of_deleting_it():
    payload = apply_symbol_issue_gate(candidate(), {"verdicts_by_symbol": {"111111": "BUY", "222222": "WATCH"}, "symbols": {}}, require_positive=True)
    effective, audit = apply_symbol_market_overlay(payload, decision=decision(), env={"TOSS_SYMBOL_RISK_OFF_SIZE_MULTIPLIER": "0.35"})
    assert audit["ordinary_buy_authorized"] is True
    assert audit["size_multiplier"] == 0.35
    assert effective["orders"][0]["quantity"] == 7
    assert effective["orders"][0]["notional_krw"] == 7000
    assert effective["orders"][0]["market_sizing_applied"] is True
    assert effective["orders"][0]["market_original_quantity"] == 20


def test_emergency_market_drop_blocks_even_positive_company_issue():
    payload = apply_symbol_issue_gate(candidate(), {"verdicts_by_symbol": {"111111": "BUY", "222222": "WATCH"}, "symbols": {}}, require_positive=True)
    effective, audit = apply_symbol_market_overlay(payload, decision=decision(market_day=-0.031), env={})
    assert audit["emergency_block"] is True
    assert effective["orders"] == []


def test_missing_market_evidence_fails_closed():
    payload = apply_symbol_issue_gate(candidate(), {"verdicts_by_symbol": {"111111": "BUY", "222222": "WATCH"}, "symbols": {}}, require_positive=True)
    effective, audit = apply_symbol_market_overlay(payload, decision=decision(evidence="MISSING"), env={})
    assert audit["emergency_block"] is True
    assert effective["orders"] == []


def test_live_submit_accepts_symbol_authorization_in_blocked_daily_regime():
    payload = apply_symbol_issue_gate(candidate(), {"verdicts_by_symbol": {"111111": "BUY", "222222": "WATCH"}, "symbols": {}}, require_positive=True)
    effective, audit = apply_symbol_market_overlay(payload, decision=decision(), env={"TOSS_SYMBOL_RISK_OFF_SIZE_MULTIPLIER": "0.35"})
    assert audit["ordinary_buy_authorized"] is True
    order = effective["orders"][0]
    assert market_regime_violation(effective, env={}) is None
    assert intraday_decision_buy_violation(order, effective, now=NOW, env={}) is None


def test_live_submit_does_not_accept_forged_order_without_symbol_issue_flag():
    payload = apply_symbol_issue_gate(candidate(), {"verdicts_by_symbol": {"111111": "BUY", "222222": "WATCH"}, "symbols": {}}, require_positive=True)
    effective, _ = apply_symbol_market_overlay(payload, decision=decision(), env={})
    order = dict(effective["orders"][0])
    order.pop("symbol_issue_authorized")
    violation = intraday_decision_buy_violation(order, effective, now=NOW, env={})
    assert violation == "intraday_verdict_mismatch:HOLD:LONG_BUY"
