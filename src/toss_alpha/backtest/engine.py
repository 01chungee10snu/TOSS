"""Research-only toy backtest engine."""
from __future__ import annotations

from toss_alpha.backtest.metrics import max_drawdown
from toss_alpha.data.schema import BacktestResult, Candle


def run_momentum_backtest(
    candles: list[Candle],
    *,
    strategy_id: str,
    starting_cash_krw: float = 1_000_000.0,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    lookback: int = 60,
) -> BacktestResult:
    ordered = sorted(candles, key=lambda c: c.close_time)
    if len(ordered) < lookback + 1:
        return BacktestResult(
            strategy_id=strategy_id,
            status="INSUFFICIENT_DATA",
            violations=[f"insufficient_history: need {lookback + 1}, got {len(ordered)}"],
            contains_live_order=False,
            research_only=True,
        )

    entry = ordered[-lookback - 1].close
    exit_price = ordered[-1].close
    if entry <= 0 or exit_price <= 0:
        return BacktestResult(
            strategy_id=strategy_id,
            status="BLOCK",
            violations=["invalid_price"],
            contains_live_order=False,
            research_only=True,
        )

    fee_rate = fee_bps / 10_000.0
    slippage_rate = slippage_bps / 10_000.0
    buy_price = entry * (1 + slippage_rate)
    sell_price = exit_price * (1 - slippage_rate)
    quantity = starting_cash_krw / buy_price
    gross_exit = quantity * sell_price
    fees = starting_cash_krw * fee_rate + gross_exit * fee_rate
    slippage = starting_cash_krw * slippage_rate + quantity * exit_price * slippage_rate
    final_value = gross_exit - fees
    total_return = (final_value - starting_cash_krw) / starting_cash_krw

    equity_curve = [starting_cash_krw]
    for candle in ordered[-lookback:]:
        equity_curve.append(quantity * candle.close)

    return BacktestResult(
        strategy_id=strategy_id,
        status="PASS",
        total_return=total_return,
        max_drawdown=max_drawdown(equity_curve),
        trades=1,
        fees_krw=fees,
        slippage_krw=slippage,
        metrics={
            "entry_price": entry,
            "exit_price": exit_price,
            "starting_cash_krw": starting_cash_krw,
            "final_value_krw": final_value,
        },
        contains_live_order=False,
        research_only=True,
    )
