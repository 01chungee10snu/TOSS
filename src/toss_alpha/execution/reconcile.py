from __future__ import annotations

from dataclasses import dataclass, field

from toss_alpha.data.schema import OrderIntent, PositionSnapshot
from toss_alpha.execution.ledger import ExecutionLedger


@dataclass(frozen=True)
class QuantityMismatch:
    symbol: str
    desired_quantity: float
    actual_quantity: float


@dataclass
class AccountReconciliationReport:
    desired_cash_krw: float
    actual_cash_krw: float | None = None
    cash_difference_krw: float | None = None
    missing_symbols: list[str] = field(default_factory=list)
    unexpected_symbols: list[str] = field(default_factory=list)
    quantity_mismatches: dict[str, QuantityMismatch] = field(default_factory=dict)
    sell_blocked_symbols: list[str] = field(default_factory=list)
    violations_by_symbol: dict[str, list[str]] = field(default_factory=dict)

    @property
    def is_match(self) -> bool:
        return not (
            self.missing_symbols
            or self.unexpected_symbols
            or self.quantity_mismatches
            or self.sell_blocked_symbols
            or (self.cash_difference_krw not in (None, 0, 0.0))
        )


def reconcile_account_state(
    *,
    desired_ledger: ExecutionLedger,
    actual_positions: list[PositionSnapshot],
    actual_cash_krw: float | None = None,
    pending_intents: list[OrderIntent] | None = None,
) -> AccountReconciliationReport:
    desired_positions = {
        symbol: position for symbol, position in desired_ledger.positions.items() if float(position.quantity) > 0
    }
    actual_by_symbol = {
        position.symbol: position for position in actual_positions if float(position.quantity) > 0
    }
    report = AccountReconciliationReport(
        desired_cash_krw=float(desired_ledger.cash_krw or 0.0),
        actual_cash_krw=None if actual_cash_krw is None else float(actual_cash_krw),
        cash_difference_krw=None if actual_cash_krw is None else float(actual_cash_krw) - float(desired_ledger.cash_krw or 0.0),
    )

    for symbol, desired_position in desired_positions.items():
        actual = actual_by_symbol.get(symbol)
        if actual is None:
            report.missing_symbols.append(symbol)
            report.violations_by_symbol.setdefault(symbol, []).append("missing_actual_position")
            continue
        if float(actual.quantity) != float(desired_position.quantity):
            report.quantity_mismatches[symbol] = QuantityMismatch(
                symbol=symbol,
                desired_quantity=float(desired_position.quantity),
                actual_quantity=float(actual.quantity),
            )
            report.violations_by_symbol.setdefault(symbol, []).append("quantity_mismatch")

    for symbol in actual_by_symbol:
        if symbol not in desired_positions:
            report.unexpected_symbols.append(symbol)
            report.violations_by_symbol.setdefault(symbol, []).append("unexpected_actual_position")

    for intent in pending_intents or []:
        if intent.side != "SELL":
            continue
        actual = actual_by_symbol.get(intent.symbol)
        if actual is None:
            report.sell_blocked_symbols.append(intent.symbol)
            report.violations_by_symbol.setdefault(intent.symbol, []).append("missing_actual_position")
            report.violations_by_symbol[intent.symbol].append("sellable_quantity_shortfall")
            continue
        requested = _intent_quantity(intent)
        sellable = float(actual.sellable_quantity if actual.sellable_quantity is not None else actual.quantity)
        if requested > sellable:
            report.sell_blocked_symbols.append(intent.symbol)
            report.violations_by_symbol.setdefault(intent.symbol, []).append("sellable_quantity_shortfall")

    report.missing_symbols.sort()
    report.unexpected_symbols.sort()
    report.sell_blocked_symbols.sort()
    return report


def _intent_quantity(intent: OrderIntent) -> float:
    if intent.quantity is None:
        raise ValueError("reconcile_account_state requires explicit quantity for SELL intents")
    return float(intent.quantity)
