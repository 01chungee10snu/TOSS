from toss_alpha.data.schema import OrderIntent
from toss_alpha.execution.paper_executor import PaperExecutor


def test_paper_executor_executes_buy_and_updates_ledger():
    executor = PaperExecutor(initial_cash_krw=1_000_000)
    intent = OrderIntent(
        strategy_id="s1",
        symbol="005930",
        side="BUY",
        quantity=5,
        reason="paper entry",
        mode="paper_auto",
    )

    result = executor.execute(intent, market_price=10_000)

    assert result.status == "FILLED"
    assert result.fill_price == 10_000
    assert executor.ledger.positions["005930"].quantity == 5
    assert executor.ledger.positions["005930"].state == "LONG"


def test_paper_executor_blocks_manual_mode_intent():
    executor = PaperExecutor(initial_cash_krw=1_000_000)
    intent = OrderIntent(
        strategy_id="s1",
        symbol="005930",
        side="BUY",
        quantity=5,
        reason="manual only",
        mode="manual_draft_only",
    )

    result = executor.execute(intent, market_price=10_000)

    assert result.status == "BLOCKED"
    assert "paper_executor_requires_paper_auto_mode" in result.violations


def test_paper_executor_requires_quantity_or_notional():
    executor = PaperExecutor(initial_cash_krw=1_000_000)
    intent = OrderIntent(
        strategy_id="s1",
        symbol="005930",
        side="BUY",
        reason="missing size",
        mode="paper_auto",
    )

    result = executor.execute(intent, market_price=10_000)

    assert result.status == "BLOCKED"
    assert "missing_order_size" in result.violations
