from __future__ import annotations

from datetime import datetime, timedelta, timezone

from toss_alpha.execution.intraday_decision import apply_intraday_decision, evaluate_intraday_decision

NOW = datetime(2026, 7, 10, 1, 30, tzinfo=timezone.utc)


def quotes(*, market_last=101.0, market_open=100.0, market_prev=100.0, inverse_last=99.0, inverse_open=100.0, inverse_prev=100.0, age=0):
    observed = (NOW - timedelta(seconds=age)).isoformat()
    return {
        "069500": {"last": market_last, "open": market_open, "prev_close": market_prev, "observed_at": observed},
        "114800": {"last": inverse_last, "open": inverse_open, "prev_close": inverse_prev, "observed_at": observed},
    }


def test_risk_context_conflicting_with_intraday_strength_holds_position():
    decision = evaluate_intraday_decision(
        daily_regime="down_high_vol",
        news_severity="critical",
        market_quotes=quotes(),
        positions=[{"symbol": "006400", "last": 437000, "open": 410000, "prev_close": 401500, "avg_price": 412500}],
        now=NOW,
    )

    assert decision["verdict"] == "HOLD"
    assert decision["signal_conflict"] is True
    assert decision["sell_symbols"] == []
    assert decision["regime_liquidation_allowed"] is False


def test_high_news_is_overridden_by_strong_two_proxy_intraday_strength():
    decision = evaluate_intraday_decision(
        daily_regime="down_high_vol",
        news_severity="high",
        market_quotes=quotes(market_last=102.0, market_open=101.0, inverse_last=98.5),
        now=NOW,
    )

    assert decision["verdict"] == "LONG_BUY"
    assert decision["reason"] == "strong_intraday_strength_overrides_risk_context"
    assert decision["market_regime"] == "risk_on"
    assert decision["signal_conflict"] is False
    assert decision["metrics"]["market_override_confirmed"] is True


def test_critical_news_requires_stronger_override_than_high_news():
    too_weak = evaluate_intraday_decision(
        daily_regime="down_high_vol",
        news_severity="critical",
        market_quotes=quotes(market_last=101.5, market_open=101.0, inverse_last=99.0),
        now=NOW,
    )
    strong = evaluate_intraday_decision(
        daily_regime="down_high_vol",
        news_severity="critical",
        market_quotes=quotes(market_last=103.0, market_open=102.0, inverse_last=98.0),
        now=NOW,
    )

    assert too_weak["verdict"] == "NO_TRADE"
    assert too_weak["signal_conflict"] is True
    assert strong["verdict"] == "LONG_BUY"
    assert strong["metrics"]["market_override_confirmed"] is True


def test_stale_news_fails_closed_even_with_fresh_bullish_quotes():
    decision = evaluate_intraday_decision(
        daily_regime="up_low_vol",
        news_severity="low",
        news_observed_at=NOW - timedelta(minutes=21),
        max_news_age_seconds=1200,
        require_fresh_news=True,
        market_quotes=quotes(),
        now=NOW,
    )

    assert decision["verdict"] == "NO_TRADE"
    assert decision["reason"] == "news_evidence_unavailable"
    assert decision["news_evidence_status"] == "STALE"
    assert decision["decision_id"].startswith("intraday-")


def test_stale_market_evidence_fails_closed():
    decision = evaluate_intraday_decision(
        daily_regime="down_high_vol", news_severity="critical", market_quotes=quotes(age=301), now=NOW
    )

    assert decision["verdict"] == "NO_TRADE"
    assert decision["evidence_status"] == "MISSING"


def test_confirmed_bear_market_without_holdings_allows_inverse_candidate():
    decision = evaluate_intraday_decision(
        daily_regime="down_high_vol",
        news_severity="high",
        market_quotes=quotes(market_last=98.5, market_open=99.5, inverse_last=101.2),
        now=NOW,
    )

    assert decision["verdict"] == "INVERSE_BUY"
    assert decision["market_regime"] == "risk_off"
    assert decision["raw_quotes"]["114800"]["last"] == 101.2


def test_extreme_but_directionally_consistent_etf_returns_proceed():
    """A genuine -10% crash with inverse +10% is a real market move, not bad data."""
    decision = evaluate_intraday_decision(
        daily_regime="down_high_vol",
        news_severity="high",
        market_quotes=quotes(
            market_last=90.2284,
            market_open=100.0,
            market_prev=100.0,
            inverse_last=1117.0,
            inverse_open=1100.0,
            inverse_prev=1017.0,
        ),
        now=NOW,
    )
    # Directionally consistent (market down + inverse up, sum ≈ 0) → not inconsistent
    assert decision["verdict"] == "INVERSE_BUY"
    assert decision["evidence_status"] == "FRESH"
    assert decision["metrics"]["intraday_bearish_override"] is True


def test_directionally_inconsistent_extreme_returns_fail_closed():
    """Both proxies falling 25% is genuinely bad data, not a real market move."""
    decision = evaluate_intraday_decision(
        daily_regime="down_high_vol",
        news_severity="high",
        market_quotes=quotes(
            market_last=75.0,
            market_open=100.0,
            market_prev=100.0,
            inverse_last=790.0,
            inverse_open=1000.0,
            inverse_prev=1050.0,
        ),
        now=NOW,
    )
    assert decision["verdict"] == "NO_TRADE"
    assert decision["reason"] == "quote_basis_inconsistent"
    assert decision["evidence_status"] == "INVALID"


def test_confirmed_bear_market_sells_only_independently_weak_losing_position():
    positions = [
        {"symbol": "006400", "last": 390000, "open": 398000, "prev_close": 400000, "avg_price": 412500},
        {"symbol": "005930", "last": 105000, "open": 100000, "prev_close": 99000, "avg_price": 90000},
    ]
    decision = evaluate_intraday_decision(
        daily_regime="down_high_vol",
        news_severity="critical",
        market_quotes=quotes(market_last=98.5, market_open=99.5, inverse_last=101.2),
        positions=positions,
        now=NOW,
    )

    assert decision["verdict"] == "SELL"
    assert decision["sell_symbols"] == ["006400"]
    assert decision["regime_liquidation_allowed"] is False


def test_confirmed_bear_market_holds_strong_position_instead_of_flipping():
    decision = evaluate_intraday_decision(
        daily_regime="down_high_vol",
        news_severity="critical",
        market_quotes=quotes(market_last=98.5, market_open=99.5, inverse_last=101.2),
        positions=[{"symbol": "006400", "last": 437000, "open": 410000, "prev_close": 401500, "avg_price": 412500}],
        now=NOW,
    )

    assert decision["verdict"] == "HOLD"
    assert decision["reason"] == "risk_off_confirmed_but_holdings_show_relative_strength"


def test_bullish_safe_context_allows_long_candidate_only_when_flat():
    flat = evaluate_intraday_decision(
        daily_regime="up_low_vol", news_severity="low", market_quotes=quotes(), now=NOW
    )
    held = evaluate_intraday_decision(
        daily_regime="up_low_vol",
        news_severity="low",
        market_quotes=quotes(),
        positions=[{"symbol": "005930"}],
        now=NOW,
    )

    assert flat["verdict"] == "LONG_BUY"
    assert held["verdict"] == "HOLD"


def test_recovery_unwinds_existing_inverse():
    decision = evaluate_intraday_decision(
        daily_regime="up_low_vol",
        news_severity="low",
        market_quotes=quotes(),
        positions=[{"symbol": "114800", "last": 990, "open": 1000, "prev_close": 1000, "avg_price": 1010}],
        now=NOW,
    )

    assert decision["verdict"] == "SELL"
    assert decision["sell_symbols"] == ["114800"]


def test_apply_decision_blocks_daily_long_on_conflict():
    payload = {"status": "CANDIDATES", "orders": [{"symbol": "006400", "side": "BUY", "quantity": 1}]}
    decision = {"verdict": "HOLD", "reason": "conflict"}

    result = apply_intraday_decision(payload, decision)

    assert result["status"] == "NO_TRADE"
    assert result["orders"] == []
    assert result["intraday_decision"]["verdict"] == "HOLD"
