from toss_alpha.data.schema import OrderIntent
from toss_alpha.execution.ledger import ExecutionLedger


def test_ledger_applies_buy_fill_and_updates_cash_position_and_average_cost():
    ledger = ExecutionLedger(initial_cash_krw=1_000_000)
    intent = OrderIntent(
        strategy_id="s1",
        symbol="005930",
        side="BUY",
        quantity=10,
        reason="paper entry",
        mode="paper_auto",
    )

    fill = ledger.record_fill(intent, fill_price=10_000, fill_quantity=10, fees_krw=500)

    assert fill.symbol == "005930"
    assert ledger.cash_krw == 899_500
    position = ledger.positions["005930"]
    assert position.quantity == 10
    assert position.avg_price == 10_000
    assert position.state == "LONG"


def test_ledger_applies_sell_fill_and_realizes_pnl():
    ledger = ExecutionLedger(initial_cash_krw=1_000_000)
    buy = OrderIntent(
        strategy_id="s1",
        symbol="005930",
        side="BUY",
        quantity=10,
        reason="paper entry",
        mode="paper_auto",
    )
    sell = OrderIntent(
        strategy_id="s1",
        symbol="005930",
        side="SELL",
        quantity=4,
        reason="paper trim",
        mode="paper_auto",
    )

    ledger.record_fill(buy, fill_price=10_000, fill_quantity=10, fees_krw=0)
    ledger.record_fill(sell, fill_price=12_000, fill_quantity=4, fees_krw=200)

    position = ledger.positions["005930"]
    assert position.quantity == 6
    assert ledger.cash_krw == 947_800
    assert round(ledger.realized_pnl_krw, 2) == 7_800


def test_ledger_rejects_sell_above_position_quantity():
    ledger = ExecutionLedger(initial_cash_krw=1_000_000)
    sell = OrderIntent(
        strategy_id="s1",
        symbol="005930",
        side="SELL",
        quantity=1,
        reason="invalid",
        mode="paper_auto",
    )

    try:
        ledger.record_fill(sell, fill_price=10_000, fill_quantity=1)
    except ValueError as exc:
        assert "sell quantity exceeds position" in str(exc)
    else:
        raise AssertionError("expected ValueError")
