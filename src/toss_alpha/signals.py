from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, pstdev


@dataclass(frozen=True)
class Signal:
    name: str
    score: float
    rationale: str


def simple_momentum_signal(closes: list[float], short: int = 20, long: int = 60) -> Signal:
    """Toy momentum signal for research only; not an investment recommendation."""
    if len(closes) < long:
        return Signal("simple_momentum", 0.0, f"insufficient_history:{len(closes)}<{long}")
    short_ma = mean(closes[-short:])
    long_ma = mean(closes[-long:])
    score = (short_ma / long_ma) - 1.0
    return Signal("simple_momentum", score, f"short_ma={short_ma:.4f}, long_ma={long_ma:.4f}")


def volatility_penalty(closes: list[float], window: int = 20) -> Signal:
    if len(closes) < window + 1:
        return Signal("volatility_penalty", 0.0, f"insufficient_history:{len(closes)}<{window+1}")
    returns = [(closes[i] / closes[i-1]) - 1.0 for i in range(len(closes)-window, len(closes))]
    vol = pstdev(returns)
    return Signal("volatility_penalty", -vol, f"daily_return_stdev={vol:.6f}")
