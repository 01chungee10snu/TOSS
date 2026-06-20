from __future__ import annotations

from dataclasses import dataclass, field

from toss_alpha.data.schema import OrderIntent
from toss_alpha.execution.ledger import ExecutionLedger, FillRecord


@dataclass(frozen=True)
class PaperExecutionResult:
    status: str
    fill_price: float | None = None
    fill_quantity: float | None = None
    violations: list[str] = field(default_factory=list)
    fill: FillRecord | None = None


@dataclass
class PaperExecutor:
    initial_cash_krw: float

    def __post_init__(self) -> None:
        self.ledger = ExecutionLedger(initial_cash_krw=float(self.initial_cash_krw))

    def execute(self, intent: OrderIntent, *, market_price: float, fees_krw: float = 0.0) -> PaperExecutionResult:
        violations: list[str] = []
        if intent.mode != "paper_auto":
            violations.append("paper_executor_requires_paper_auto_mode")
        if intent.quantity is None and intent.notional_krw is None:
            violations.append("missing_order_size")
        if violations:
            return PaperExecutionResult(status="BLOCKED", violations=violations)

        try:
            fill = self.ledger.record_fill(intent, fill_price=market_price, fees_krw=fees_krw)
        except ValueError as exc:
            return PaperExecutionResult(status="BLOCKED", violations=[str(exc)])

        return PaperExecutionResult(
            status="FILLED",
            fill_price=fill.fill_price,
            fill_quantity=fill.fill_quantity,
            fill=fill,
        )
