"""Canonical data contracts for the TOSS research harness."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

HarnessMode = Literal["research_only", "backtest_only", "paper_only", "manual_draft_only"]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str = ""
    market: str = "KR"
    currency: str = "KRW"
    asset_type: str = "stock"
    tradable: bool = True
    source: str = "unknown"
    as_of: datetime = field(default_factory=_now_utc)


@dataclass(frozen=True)
class Quote:
    symbol: str
    timestamp: datetime
    last: float
    bid: float | None = None
    ask: float | None = None
    volume: float | None = None
    source: str = "unknown"
    freshness_ms: int | None = None


@dataclass(frozen=True)
class Candle:
    symbol: str
    interval: str
    open_time: datetime
    close_time: datetime
    close: float
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None
    adjusted: bool = False
    corporate_action_adjusted: bool = False
    source: str = "unknown"


@dataclass(frozen=True)
class AccountSnapshot:
    account_id: str
    currency: str = "KRW"
    cash: float | None = None
    buying_power: float | None = None
    total_equity: float | None = None
    as_of: datetime = field(default_factory=_now_utc)
    source: str = "toss"


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    quantity: float
    sellable_quantity: float | None = None
    avg_price: float | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None
    currency: str = "KRW"
    as_of: datetime = field(default_factory=_now_utc)
    source: str = "toss"


@dataclass(frozen=True)
class DisclosureEvent:
    symbol: str
    event_type: str
    title: str
    reported_at: datetime
    available_at: datetime
    url: str | None = None
    revision_flag: bool = False
    source: str = "opendart"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResearchGoal:
    goal_id: str
    mode: HarnessMode
    symbols: list[str]
    start: str
    end: str
    strategy_name: str
    strategy_params: dict[str, Any] = field(default_factory=dict)
    risk_profile: str = "conservative"


@dataclass(frozen=True)
class SignalResult:
    name: str
    score: float
    rationale: str
    research_only: bool = True
    not_investment_advice: bool = True
    data_as_of: datetime | None = None
    known_limitations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class OrderIntent:
    strategy_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    reason: str
    notional_krw: float | None = None
    quantity: float | None = None
    order_type: str = "MARKET"
    limit_price: float | None = None
    time_in_force: str = "DAY"
    mode: Literal["manual_draft_only"] = "manual_draft_only"
    not_live_order: bool = True
    intent_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=_now_utc)
    expires_at: datetime | None = None


@dataclass(frozen=True)
class RiskDecision:
    allow: bool
    status: Literal["ALLOW", "BLOCK"]
    violations: list[str] = field(default_factory=list)
    checked_policy_version: str = "unknown"
    checked_data_snapshot_id: str | None = None
    timestamp: datetime = field(default_factory=_now_utc)

    @classmethod
    def blocked(cls, violations: list[str]) -> "RiskDecision":
        return cls(allow=False, status="BLOCK", violations=list(violations))

    @classmethod
    def allowed(cls) -> "RiskDecision":
        return cls(allow=True, status="ALLOW")


@dataclass(frozen=True)
class BacktestResult:
    strategy_id: str
    status: Literal["PASS", "BLOCK", "INSUFFICIENT_DATA"]
    total_return: float = 0.0
    max_drawdown: float = 0.0
    trades: int = 0
    fees_krw: float = 0.0
    slippage_krw: float = 0.0
    metrics: dict[str, float] = field(default_factory=dict)
    violations: list[str] = field(default_factory=list)
    contains_live_order: bool = False
    research_only: bool = True
