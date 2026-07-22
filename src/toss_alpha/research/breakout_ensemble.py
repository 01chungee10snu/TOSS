"""Leakage-safe primitives for non-overlapping breakout ensemble backtests."""

from __future__ import annotations

import numpy as np


def freeze_topk(
    scores: np.ndarray,
    rank_eligible: np.ndarray,
    execution_gate: np.ndarray,
    k: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Freeze top-k using only rank-time eligibility, then apply execution gate.

    Failed execution slots remain cash. They are never replaced by lower-ranked
    names after observing entry-session information.
    """
    scores = np.asarray(scores, dtype=float)
    rank_eligible = np.asarray(rank_eligible, dtype=bool)
    execution_gate = np.asarray(execution_gate, dtype=bool)
    if not (scores.ndim == rank_eligible.ndim == execution_gate.ndim == 1):
        raise ValueError("scores and masks must be one-dimensional")
    if not (len(scores) == len(rank_eligible) == len(execution_gate)):
        raise ValueError("scores and masks must have equal length")
    if k <= 0:
        raise ValueError("k must be positive")

    n = len(scores)
    order = np.argsort(np.where(rank_eligible & np.isfinite(scores), scores, -np.inf))[::-1]
    if n >= k:
        idx = order[:k]
    else:
        idx = np.pad(order, (0, k - n), constant_values=0)
    rank_valid = rank_eligible[idx] & np.isfinite(scores[idx])
    valid = rank_valid & execution_gate[idx]
    return idx.astype(np.int64), valid


def nonoverlap_signal_indices(start: int, stop: int, hold_days: int) -> np.ndarray:
    """Return signal rows spaced exactly by the holding horizon."""
    if hold_days <= 0:
        raise ValueError("hold_days must be positive")
    if stop <= start:
        return np.array([], dtype=np.int64)
    return np.arange(start, stop, hold_days, dtype=np.int64)


def traded_sides(
    prev_idx: np.ndarray,
    prev_valid: np.ndarray,
    curr_idx: np.ndarray,
    curr_valid: np.ndarray,
) -> int:
    """Count one-way entry/exit sides after netting valid repeated positions."""
    prev = set(np.asarray(prev_idx)[np.asarray(prev_valid, dtype=bool)].tolist())
    curr = set(np.asarray(curr_idx)[np.asarray(curr_valid, dtype=bool)].tolist())
    return len(prev - curr) + len(curr - prev)
