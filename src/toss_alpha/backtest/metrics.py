"""Minimal deterministic metrics for research-only backtests."""
from __future__ import annotations


def max_drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            worst = min(worst, (value - peak) / peak)
    return worst
