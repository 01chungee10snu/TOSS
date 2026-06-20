from __future__ import annotations

import json
from dataclasses import dataclass

from toss_alpha.data.schema import OrderIntent
from toss_alpha.execution.paper_executor import PaperExecutionResult, PaperExecutor


@dataclass(frozen=True)
class HoldingSeed:
    symbol: str
    quantity: float
    avg_price: float

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "HoldingSeed":
        return cls(
            symbol=str(payload["symbol"]),
            quantity=float(payload["quantity"]),
            avg_price=float(payload["avg_price"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "quantity": self.quantity,
            "avg_price": self.avg_price,
        }


@dataclass(frozen=True)
class DailyPaperOrder:
    intent: OrderIntent
    market_price: float
    fees_krw: float = 0.0

    @classmethod
    def from_dict(cls, payload: dict[str, object], *, strategy_id: str) -> "DailyPaperOrder":
        intent = OrderIntent(
            strategy_id=strategy_id,
            symbol=str(payload["symbol"]),
            side=str(payload["side"]),
            quantity=float(payload["quantity"]) if payload.get("quantity") is not None else None,
            notional_krw=float(payload["notional_krw"]) if payload.get("notional_krw") is not None else None,
            reason=str(payload.get("reason") or "daily paper order"),
            mode="paper_auto",
        )
        return cls(
            intent=intent,
            market_price=float(payload["market_price"]),
            fees_krw=float(payload.get("fees_krw") or 0.0),
        )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "symbol": self.intent.symbol,
            "side": self.intent.side,
            "reason": self.intent.reason,
            "market_price": self.market_price,
            "fees_krw": self.fees_krw,
        }
        if self.intent.quantity is not None:
            payload["quantity"] = self.intent.quantity
        if self.intent.notional_krw is not None:
            payload["notional_krw"] = self.intent.notional_krw
        return payload


@dataclass(frozen=True)
class DailyPaperPlan:
    initial_cash_krw: float
    holdings: list[HoldingSeed]
    orders: list[DailyPaperOrder]

    @classmethod
    def from_dict(cls, payload: dict[str, object], *, strategy_id: str = "daily-paper") -> "DailyPaperPlan":
        return cls(
            initial_cash_krw=float(payload.get("initial_cash_krw") or 0.0),
            holdings=[HoldingSeed.from_dict(item) for item in payload.get("holdings", [])],
            orders=[DailyPaperOrder.from_dict(item, strategy_id=strategy_id) for item in payload.get("orders", [])],
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "initial_cash_krw": self.initial_cash_krw,
                "holdings": [item.to_dict() for item in self.holdings],
                "orders": [item.to_dict() for item in self.orders],
            }
        )


@dataclass
class DailyPaperExecutionResult:
    status: str
    total_orders: int
    filled_orders: int
    blocked_orders: int
    order_results: list[PaperExecutionResult]
    executor: PaperExecutor

    @property
    def ledger(self):
        return self.executor.ledger


def run_daily_paper(plan: DailyPaperPlan) -> DailyPaperExecutionResult:
    executor = PaperExecutor(initial_cash_krw=float(plan.initial_cash_krw))
    for holding in plan.holdings:
        executor.ledger.seed_position(
            symbol=holding.symbol,
            quantity=float(holding.quantity),
            avg_price=float(holding.avg_price),
        )

    order_results: list[PaperExecutionResult] = []
    filled_orders = 0
    blocked_orders = 0
    for order in plan.orders:
        result = executor.execute(order.intent, market_price=order.market_price, fees_krw=order.fees_krw)
        order_results.append(result)
        if result.status == "FILLED":
            filled_orders += 1
        else:
            blocked_orders += 1

    return DailyPaperExecutionResult(
        status="OK",
        total_orders=len(plan.orders),
        filled_orders=filled_orders,
        blocked_orders=blocked_orders,
        order_results=order_results,
        executor=executor,
    )
