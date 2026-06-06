from datetime import datetime, timezone

import pytest

from toss_alpha.data.schema import Candle, OrderIntent, RiskDecision, SignalResult


def test_candle_requires_core_fields():
    now = datetime.now(timezone.utc)
    candle = Candle(
        symbol="005930",
        interval="1D",
        open_time=now,
        close_time=now,
        close=70000.0,
    )
    assert candle.symbol == "005930"
    assert candle.interval == "1D"
    assert candle.source == "unknown"

    with pytest.raises(TypeError):
        Candle(symbol="005930", interval="1D", close=70000.0)


def test_signal_result_defaults_to_research_only():
    signal = SignalResult(name="momentum", score=0.1, rationale="test")
    assert signal.research_only is True
    assert signal.not_investment_advice is True


def test_risk_decision_blocked_preserves_violations():
    decision = RiskDecision.blocked(["live_trading_disabled", "missing_data"])
    assert decision.allow is False
    assert decision.status == "BLOCK"
    assert decision.violations == ["live_trading_disabled", "missing_data"]


def test_order_intent_defaults_to_manual_draft_only():
    intent = OrderIntent(
        strategy_id="s1",
        symbol="005930",
        side="BUY",
        notional_krw=100000,
        reason="research candidate",
    )
    assert intent.mode == "manual_draft_only"
    assert intent.not_live_order is True
