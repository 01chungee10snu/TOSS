from __future__ import annotations

from dataclasses import dataclass, field

from toss_alpha.data.schema import OrderIntent, PositionState


@dataclass
class LedgerPosition:
    symbol: str
    quantity: float = 0.0
    avg_price: float = 0.0
    state: PositionState = "FLAT"


@dataclass(frozen=True)
class FillRecord:
    intent_id: str
    symbol: str
    side: str
    fill_price: float
    fill_quantity: float
    gross_notional_krw: float
    fees_krw: float = 0.0
    realized_pnl_krw: float = 0.0


@dataclass
class ExecutionLedger:
    initial_cash_krw: float
    cash_krw: float | None = None
    realized_pnl_krw: float = 0.0
    positions: dict[str, LedgerPosition] = field(default_factory=dict)
    fills: list[FillRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.cash_krw is None:
            self.cash_krw = float(self.initial_cash_krw)

    def record_fill(
        self,
        intent: OrderIntent,
        *,
        fill_price: float,
        fill_quantity: float | None = None,
        fees_krw: float = 0.0,
    ) -> FillRecord:
        quantity = self._resolve_quantity(intent, fill_price=fill_price, fill_quantity=fill_quantity)
        gross = fill_price * quantity
        fees = float(fees_krw)
        position = self.positions.get(intent.symbol, LedgerPosition(symbol=intent.symbol))

        if intent.side == "BUY":
            total_cost = gross + fees
            if (self.cash_krw or 0.0) < total_cost:
                raise ValueError("insufficient cash for buy fill")
            new_quantity = position.quantity + quantity
            prior_cost_basis = position.avg_price * position.quantity
            new_avg = (prior_cost_basis + gross) / new_quantity if new_quantity else 0.0
            position.quantity = new_quantity
            position.avg_price = new_avg
            position.state = "LONG"
            self.cash_krw = (self.cash_krw or 0.0) - total_cost
            realized = 0.0
        elif intent.side == "SELL":
            if quantity > position.quantity:
                raise ValueError("sell quantity exceeds position")
            proceeds = gross - fees
            realized = ((fill_price - position.avg_price) * quantity) - fees
            position.quantity -= quantity
            position.state = "LONG" if position.quantity > 0 else "FLAT"
            self.cash_krw = (self.cash_krw or 0.0) + proceeds
            self.realized_pnl_krw += realized
        else:
            raise ValueError(f"unsupported side: {intent.side}")

        self.positions[intent.symbol] = position
        fill = FillRecord(
            intent_id=intent.intent_id,
            symbol=intent.symbol,
            side=intent.side,
            fill_price=fill_price,
            fill_quantity=quantity,
            gross_notional_krw=gross,
            fees_krw=fees,
            realized_pnl_krw=realized,
        )
        self.fills.append(fill)
        return fill

    def seed_position(self, *, symbol: str, quantity: float, avg_price: float) -> LedgerPosition:
        seeded = LedgerPosition(
            symbol=symbol,
            quantity=float(quantity),
            avg_price=float(avg_price),
            state="LONG" if float(quantity) > 0 else "FLAT",
        )
        self.positions[symbol] = seeded
        return seeded

    @staticmethod
    def _resolve_quantity(intent: OrderIntent, *, fill_price: float, fill_quantity: float | None) -> float:
        if fill_quantity is not None:
            quantity = float(fill_quantity)
        elif intent.quantity is not None:
            quantity = float(intent.quantity)
        elif intent.notional_krw is not None:
            quantity = float(intent.notional_krw) / float(fill_price)
        else:
            raise ValueError("missing order size")
        if quantity <= 0:
            raise ValueError("fill quantity must be positive")
        return quantity
