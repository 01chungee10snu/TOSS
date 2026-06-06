from toss_alpha.risk import RiskPolicy, validate_order_intent


def test_live_trading_disabled_blocks_order():
    violations = validate_order_intent(
        side="BUY",
        notional_krw=10_000,
        portfolio_value_krw=1_000_000,
        policy=RiskPolicy(live_trading_enabled=False),
        manual_confirmation=True,
    )
    assert "live_trading_disabled" in violations


def test_order_size_and_manual_confirmation_are_checked():
    violations = validate_order_intent(
        side="BUY",
        notional_krw=200_000,
        portfolio_value_krw=1_000_000,
        policy=RiskPolicy(live_trading_enabled=True, max_order_krw=100_000),
        manual_confirmation=False,
    )
    assert "manual_confirmation_required" in violations
    assert "max_order_krw_exceeded" in violations
    assert "max_position_pct_exceeded" in violations
