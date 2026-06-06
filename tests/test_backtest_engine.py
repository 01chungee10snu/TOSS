from datetime import datetime, timedelta, timezone

from toss_alpha.backtest.engine import run_momentum_backtest
from toss_alpha.data.schema import Candle


def _candles(count: int, start: float = 100.0, step: float = 1.0):
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [
        Candle(
            symbol="005930",
            interval="1D",
            open_time=base + timedelta(days=i),
            close_time=base + timedelta(days=i + 1),
            close=start + i * step,
        )
        for i in range(count)
    ]


def test_insufficient_data_returns_blocked_result():
    result = run_momentum_backtest(_candles(10), strategy_id="s1")
    assert result.status == "INSUFFICIENT_DATA"
    assert "insufficient_history" in result.violations[0]
    assert result.contains_live_order is False


def test_deterministic_candles_produce_deterministic_result():
    result1 = run_momentum_backtest(_candles(80), strategy_id="s1", starting_cash_krw=1_000_000)
    result2 = run_momentum_backtest(_candles(80), strategy_id="s1", starting_cash_krw=1_000_000)
    assert result1 == result2
    assert result1.status == "PASS"
    assert result1.trades == 1
    assert result1.total_return > 0


def test_result_includes_fees_and_slippage_and_no_live_order():
    result = run_momentum_backtest(
        _candles(80),
        strategy_id="s1",
        starting_cash_krw=1_000_000,
        fee_bps=10,
        slippage_bps=5,
    )
    assert result.fees_krw > 0
    assert result.slippage_krw > 0
    assert result.contains_live_order is False
    assert result.research_only is True
