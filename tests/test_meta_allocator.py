from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from toss_alpha.research.meta_allocator import (
    RiskMetrics,
    allocate_candidates,
    correlation_matrix,
    drawdown_scale,
    pairwise_correlation,
    prune_correlated,
    risk_metrics,
)


def _series(values) -> pd.Series:
    idx = pd.bdate_range("2024-01-02", periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


def _row(strategy_id: str, *, rank: int, status: str = "PAPER_CANDIDATE", grade: str = "B") -> dict:
    return {
        "strategy_id": strategy_id,
        "rank": rank,
        "status": status,
        "evidence_grade": grade,
        "performance_score": 99.0,
    }


def test_pairwise_and_downside_correlation_detect_duplicate_series():
    x = np.sin(np.arange(400) / 13.0) * 0.01
    a = _series(x)
    b = _series(x * 1.2)
    result = pairwise_correlation(a, b, min_obs=20)

    assert result["observations"] == 400
    assert result["pearson"] == pytest.approx(1.0)
    assert result["downside"] == pytest.approx(1.0)


def test_correlation_matrix_keeps_negative_correlation_as_diversification():
    x = np.sin(np.arange(400) / 11.0) * 0.01
    matrix = correlation_matrix({"a": _series(x), "b": _series(-x)}, min_obs=20, windows=(60, 120))
    row = matrix["pairs"]["a|b"]

    assert row["pearson"] == pytest.approx(-1.0)
    assert row["rolling_latest"]["60"] == pytest.approx(-1.0)
    pruned = prune_correlated(["a", "b"], correlations=matrix, rank={"a": 1, "b": 2}, threshold=0.95)
    assert pruned["kept"] == ["a", "b"]
    assert pruned["removed"] == {}


def test_protected_forward_target_wins_correlation_pruning_even_with_worse_rank():
    x = np.sin(np.arange(400) / 17.0) * 0.01
    matrix = correlation_matrix({"rank1": _series(x), "forward": _series(x * 0.9)}, min_obs=20)
    result = prune_correlated(
        ["rank1", "forward"],
        correlations=matrix,
        rank={"rank1": 1, "forward": 2},
        protected=["forward"],
        threshold=0.95,
    )

    assert result["kept"] == ["forward"]
    assert result["removed"]["rank1"]["duplicate_of"] == "forward"


def test_live_allocation_is_zero_for_paper_candidates_regardless_of_performance_score():
    rows = [_row("paper_a", rank=1), _row("paper_b", rank=2)]
    returns = {
        "paper_a": _series(np.sin(np.arange(400) / 9.0) * 0.01 + 0.0002),
        "paper_b": _series(np.cos(np.arange(400) / 15.0) * 0.008 + 0.0002),
    }
    metrics = {k: risk_metrics(v) for k, v in returns.items()}
    corr = correlation_matrix(returns, min_obs=20)

    result = allocate_candidates(rows, metrics=metrics, correlations=corr, mode="live")

    assert result["weights"] == {}
    assert result["cash_weight"] == 1.0
    assert set(result["blocked"]) == {"paper_a", "paper_b"}
    assert all("status_not_live_eligible" in reasons for reasons in result["blocked"].values())
    assert result["policy"]["performance_score_used_for_sizing"] is False


def test_research_shadow_uses_correlation_pruning_inverse_vol_and_caps_weight():
    n = 500
    a = _series(np.sin(np.arange(n) / 8.0) * 0.012 + 0.0003)
    duplicate = _series(a.values * 0.95)
    diversifier = _series(np.cos(np.arange(n) / 19.0) * 0.004 + 0.00015)
    rows = [_row("rank1", rank=1), _row("forward", rank=2), _row("diversifier", rank=3)]
    returns = {"rank1": a, "forward": duplicate, "diversifier": diversifier}
    metrics = {k: risk_metrics(v) for k, v in returns.items()}
    corr = correlation_matrix(returns, min_obs=20)

    result = allocate_candidates(
        rows,
        metrics=metrics,
        correlations=corr,
        mode="research_shadow",
        protected=["forward"],
        correlation_threshold=0.95,
        max_strategy_weight=0.50,
        min_observations=252,
    )

    assert result["selected_after_correlation"] == ["forward", "diversifier"]
    assert "rank1" in result["correlation_pruning"]["removed"]
    assert set(result["weights"]) == {"forward", "diversifier"}
    assert max(result["weights"].values()) <= 0.50 + 1e-9
    assert sum(result["weights"].values()) + result["cash_weight"] == pytest.approx(1.0)


def test_drawdown_soft_and_hard_stops_reduce_total_risk_budget():
    assert drawdown_scale(-0.05) == 1.0
    assert drawdown_scale(-0.12) == 0.5
    assert drawdown_scale(-0.25) == 0.0
    rows = [_row("a", rank=1), _row("b", rank=2)]
    metrics = {
        "a": RiskMetrics(400, 0.10, 1.0, -0.15, -0.12, 0.08),
        "b": RiskMetrics(400, 0.10, 1.0, -0.25, -0.25, 0.08),
    }
    corr = {"pairs": {"a|b": {"pearson": 0.0, "downside": 0.0}}}

    result = allocate_candidates(
        rows,
        metrics=metrics,
        correlations=corr,
        mode="research_shadow",
        current_drawdowns={"a": -0.12, "b": -0.25},
    )

    assert result["risk_scales"] == {"a": 0.5, "b": 0.0}
    assert result["invested_weight"] < 1.0
    assert result["cash_weight"] > 0.0
    assert "hard_drawdown_stop_or_missing_drawdown" in result["blocked"]["b"]


def test_missing_daily_series_is_blocked_fail_closed():
    rows = [_row("no_series", rank=1, status="LIVE_ELIGIBLE", grade="A")]
    result = allocate_candidates(rows, metrics={}, correlations={"pairs": {}}, mode="live")
    assert result["weights"] == {}
    assert result["cash_weight"] == 1.0
    assert "insufficient_daily_return_history" in result["blocked"]["no_series"]
