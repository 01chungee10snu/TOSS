from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskPolicy:
    live_trading_enabled: bool = False
    max_position_pct: float = 0.05
    max_daily_loss_pct: float = 0.01
    max_order_krw: int = 100_000
    require_manual_confirmation: bool = True
    allow_short_selling: bool = False
    allow_leverage: bool = False
    allow_options: bool = False


def validate_order_intent(
    *,
    side: str,
    notional_krw: float,
    portfolio_value_krw: float,
    policy: RiskPolicy,
    manual_confirmation: bool = False,
) -> list[str]:
    """Return blocking risk violations for a prospective order.

    This is deliberately conservative. Live orders should be impossible unless
    a caller explicitly enables live trading and passes manual confirmation.
    """
    violations: list[str] = []
    side = side.upper()
    if not policy.live_trading_enabled:
        violations.append("live_trading_disabled")
    if policy.require_manual_confirmation and not manual_confirmation:
        violations.append("manual_confirmation_required")
    if side not in {"BUY", "SELL"}:
        violations.append("invalid_side")
    if side == "SELL" and not policy.allow_short_selling:
        # A production system must also check sellable quantity before SELL.
        violations.append("sell_requires_sellable_quantity_check")
    if notional_krw > policy.max_order_krw:
        violations.append("max_order_krw_exceeded")
    if portfolio_value_krw <= 0:
        violations.append("invalid_portfolio_value")
    elif notional_krw / portfolio_value_krw > policy.max_position_pct:
        violations.append("max_position_pct_exceeded")
    return violations
