import json

from toss_alpha.data.schema import OrderIntent
from toss_alpha.execution.daily_paper import DailyPaperOrder, DailyPaperPlan, HoldingSeed, run_daily_paper


def test_daily_paper_executes_batch_with_seeded_holdings_and_summary():
    plan = DailyPaperPlan(
        initial_cash_krw=700_000,
        holdings=[HoldingSeed(symbol="005930", quantity=5, avg_price=10_000)],
        orders=[
            DailyPaperOrder(
                intent=OrderIntent(
                    strategy_id="daily-1",
                    symbol="005930",
                    side="SELL",
                    quantity=2,
                    reason="trim winner",
                    mode="paper_auto",
                ),
                market_price=12_000,
                fees_krw=100,
            ),
            DailyPaperOrder(
                intent=OrderIntent(
                    strategy_id="daily-1",
                    symbol="000660",
                    side="BUY",
                    quantity=3,
                    reason="new entry",
                    mode="paper_auto",
                ),
                market_price=50_000,
                fees_krw=200,
            ),
        ],
    )

    result = run_daily_paper(plan)

    assert result.status == "OK"
    assert result.total_orders == 2
    assert result.filled_orders == 2
    assert result.blocked_orders == 0
    assert result.ledger.cash_krw == 573_700
    assert result.ledger.realized_pnl_krw == 3_900
    assert result.ledger.positions["005930"].quantity == 3
    assert result.ledger.positions["000660"].quantity == 3


def test_daily_paper_keeps_running_when_one_order_is_blocked():
    plan = DailyPaperPlan(
        initial_cash_krw=200_000,
        holdings=[HoldingSeed(symbol="005930", quantity=1, avg_price=10_000)],
        orders=[
            DailyPaperOrder(
                intent=OrderIntent(
                    strategy_id="daily-2",
                    symbol="005930",
                    side="SELL",
                    quantity=3,
                    reason="oversell",
                    mode="paper_auto",
                ),
                market_price=11_000,
            ),
            DailyPaperOrder(
                intent=OrderIntent(
                    strategy_id="daily-2",
                    symbol="000660",
                    side="BUY",
                    quantity=1,
                    reason="valid buy",
                    mode="paper_auto",
                ),
                market_price=90_000,
            ),
        ],
    )

    result = run_daily_paper(plan)

    assert result.status == "OK"
    assert result.total_orders == 2
    assert result.filled_orders == 1
    assert result.blocked_orders == 1
    assert result.order_results[0].status == "BLOCKED"
    assert "sell quantity exceeds position" in result.order_results[0].violations[0]
    assert result.order_results[1].status == "FILLED"
    assert result.ledger.cash_krw == 110_000
    assert result.ledger.positions["005930"].quantity == 1
    assert result.ledger.positions["000660"].quantity == 1


def test_daily_paper_plan_roundtrip_from_json_payload():
    payload = {
        "initial_cash_krw": 500_000,
        "holdings": [{"symbol": "005930", "quantity": 2, "avg_price": 10_000}],
        "orders": [
            {
                "symbol": "005930",
                "side": "SELL",
                "quantity": 1,
                "reason": "take partial",
                "market_price": 11_500,
                "fees_krw": 50,
            }
        ],
    }

    plan = DailyPaperPlan.from_dict(payload, strategy_id="cli-daily-paper")

    assert plan.initial_cash_krw == 500_000
    assert plan.holdings[0].symbol == "005930"
    assert plan.orders[0].intent.strategy_id == "cli-daily-paper"
    assert plan.orders[0].intent.mode == "paper_auto"
    assert plan.orders[0].market_price == 11_500
    assert plan.orders[0].fees_krw == 50
    assert json.loads(plan.to_json())["orders"][0]["symbol"] == "005930"
