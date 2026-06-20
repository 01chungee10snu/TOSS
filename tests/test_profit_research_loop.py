from __future__ import annotations

import pandas as pd

from toss_alpha.research.profit_loop import (
    apply_extra_cost_bps,
    build_fast_veto_grid,
    build_walkforward_folds,
    choose_best_branch,
    evaluate_fast_veto_variant,
    evaluate_fixed_variant_walkforward,
    run_walkforward_variant_selection,
    summarize_picks_performance,
    walkforward_candidate_gate,
)


def test_summarize_picks_performance_compounds_group_mean_returns():
    picks = pd.DataFrame(
        [
            {"week_key": "2024-01-01/2024-01-05", "trade_return": 0.10},
            {"week_key": "2024-01-01/2024-01-05", "trade_return": 0.00},
            {"week_key": "2024-01-08/2024-01-12", "trade_return": -0.10},
        ]
    )

    perf = summarize_picks_performance(picks, group_col="week_key")

    assert perf["periods"] == 2
    assert perf["total_trades"] == 3
    assert round(perf["total_return_pct"], 2) == -5.5
    assert round(perf["max_drawdown_pct"], 2) == -10.0
    assert perf["win_rate_pct"] == 50.0


def test_apply_extra_cost_bps_reduces_trade_return_by_round_trip_cost():
    picks = pd.DataFrame([
        {"trade_return": 0.0500, "week_key": "2024-W01"},
        {"trade_return": -0.0100, "week_key": "2024-W02"},
    ])

    stressed = apply_extra_cost_bps(picks, extra_round_trip_bps=15.0)

    assert round(float(stressed.loc[0, "trade_return"]), 6) == 0.0485
    assert round(float(stressed.loc[1, "trade_return"]), 6) == -0.0115


def test_evaluate_fast_veto_variant_filters_symbols_by_thresholds():
    picks = pd.DataFrame(
        [
            {
                "Date": "2024-01-01",
                "week_key": "2024-01-01/2024-01-05",
                "code": "111111",
                "trade_return": 0.08,
            },
            {
                "Date": "2024-01-01",
                "week_key": "2024-01-01/2024-01-05",
                "code": "222222",
                "trade_return": 0.03,
            },
            {
                "Date": "2024-01-08",
                "week_key": "2024-01-08/2024-01-12",
                "code": "111111",
                "trade_return": -0.02,
            },
        ]
    )
    panel = pd.DataFrame(
        [
            {"Date": "2023-12-29", "code": "111111", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000000},
            {"Date": "2024-01-01", "code": "111111", "Open": 102, "High": 103, "Low": 101, "Close": 102, "Volume": 1000000},
            {"Date": "2024-01-05", "code": "111111", "Open": 101, "High": 101, "Low": 98, "Close": 99, "Volume": 1000000},
            {"Date": "2024-01-08", "code": "111111", "Open": 100, "High": 101, "Low": 98, "Close": 99, "Volume": 1000000},
            {"Date": "2023-12-29", "code": "222222", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},
            {"Date": "2024-01-01", "code": "222222", "Open": 103, "High": 120, "Low": 90, "Close": 103, "Volume": 1000},
        ]
    )
    thresholds = {
        "max_gap_pct": 0.05,
        "max_intraday_range_pct": 0.20,
        "min_dollar_volume_krw": 1_000_000,
        "max_prev_volatility_20d": 1.0,
    }

    result = evaluate_fast_veto_variant(picks=picks, panel=panel, thresholds=thresholds, group_col="week_key")

    assert result["kept_trades"] == 2
    assert result["blocked_trades"] == 1
    assert result["blocked_counts_by_reason"]["low_dollar_volume"] == 1
    assert result["performance"]["total_trades"] == 2
    assert round(result["performance"]["total_return_pct"], 2) == 5.84


def test_choose_best_branch_prefers_positive_return_with_better_risk_adjusted_score():
    best = choose_best_branch(
        [
            {
                "branch_id": "daily_contextual",
                "cycle": "daily",
                "method": "baseline",
                "performance": {"total_return_pct": 45.57, "max_drawdown_pct": -19.15, "sharpe_proxy": 0.61, "periods": 487, "total_trades": 487},
            },
            {
                "branch_id": "monfri_fast_veto",
                "cycle": "monfri",
                "method": "fast_veto_frontier",
                "performance": {"total_return_pct": 62.0, "max_drawdown_pct": -12.0, "sharpe_proxy": 1.1, "periods": 30, "total_trades": 80},
            },
        ]
    )

    assert best["branch_id"] == "monfri_fast_veto"
    assert best["recommendation"] == "promote_to_next_replay"


def test_build_fast_veto_grid_contains_conservative_and_looser_variants():
    grid = build_fast_veto_grid()

    assert {row["variant_id"] for row in grid} >= {"veto_base", "veto_looser_range", "veto_higher_liquidity", "veto_higher_liquidity_looser_range"}


def test_build_walkforward_folds_creates_expanding_year_splits():
    folds = build_walkforward_folds([2022, 2023, 2024, 2025], min_train_years=1)

    assert folds == [
        {"train_years": [2022], "test_year": 2023},
        {"train_years": [2022, 2023], "test_year": 2024},
        {"train_years": [2022, 2023, 2024], "test_year": 2025},
    ]


def test_run_walkforward_variant_selection_uses_train_winner_and_scores_oos():
    picks = pd.DataFrame(
        [
            {"Date": "2022-01-03", "week_key": "2022-W01", "code": "111111", "trade_return": 0.10},
            {"Date": "2022-01-03", "week_key": "2022-W01", "code": "222222", "trade_return": -0.05},
            {"Date": "2023-01-02", "week_key": "2023-W01", "code": "111111", "trade_return": 0.08},
            {"Date": "2023-01-02", "week_key": "2023-W01", "code": "222222", "trade_return": -0.20},
            {"Date": "2024-01-01", "week_key": "2024-W01", "code": "111111", "trade_return": 0.01},
            {"Date": "2024-01-01", "week_key": "2024-W01", "code": "222222", "trade_return": 0.02},
        ]
    )
    panel = pd.DataFrame(
        [
            {"Date": "2021-12-31", "code": "111111", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000000},
            {"Date": "2022-01-03", "code": "111111", "Open": 101, "High": 102, "Low": 100, "Close": 101, "Volume": 1000000},
            {"Date": "2022-01-07", "code": "111111", "Open": 101, "High": 101, "Low": 100, "Close": 101, "Volume": 1000000},
            {"Date": "2021-12-31", "code": "222222", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},
            {"Date": "2022-01-03", "code": "222222", "Open": 102, "High": 103, "Low": 99, "Close": 102, "Volume": 1000},
            {"Date": "2022-01-07", "code": "222222", "Open": 102, "High": 102, "Low": 98, "Close": 99, "Volume": 1000},
            {"Date": "2022-12-30", "code": "111111", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000000},
            {"Date": "2023-01-02", "code": "111111", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000000},
            {"Date": "2023-01-06", "code": "111111", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000000},
            {"Date": "2022-12-30", "code": "222222", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},
            {"Date": "2023-01-02", "code": "222222", "Open": 102, "High": 110, "Low": 90, "Close": 103, "Volume": 1000},
            {"Date": "2023-01-06", "code": "222222", "Open": 103, "High": 103, "Low": 102, "Close": 103, "Volume": 1000},
            {"Date": "2023-12-29", "code": "111111", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000000},
            {"Date": "2024-01-01", "code": "111111", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000000},
            {"Date": "2024-01-05", "code": "111111", "Open": 100, "High": 100, "Low": 99, "Close": 100, "Volume": 1000000},
            {"Date": "2023-12-29", "code": "222222", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},
            {"Date": "2024-01-01", "code": "222222", "Open": 102, "High": 110, "Low": 90, "Close": 102, "Volume": 1000},
            {"Date": "2024-01-05", "code": "222222", "Open": 102, "High": 102, "Low": 101, "Close": 102, "Volume": 1000},
        ]
    )
    variants = [
        {"variant_id": "baseline", "thresholds": None},
        {
            "variant_id": "high_liquidity_only",
            "thresholds": {
                "max_gap_pct": 0.05,
                "max_intraday_range_pct": 0.20,
                "min_dollar_volume_krw": 1_000_000,
                "max_prev_volatility_20d": 1.0,
            },
        },
    ]

    result = run_walkforward_variant_selection(
        picks=picks,
        panel=panel,
        variants=variants,
        group_col="week_key",
        min_train_years=2,
    )

    assert [fold["selected_variant_id"] for fold in result["folds"]] == ["high_liquidity_only"]
    assert result["aggregate_oos"]["periods"] == 1
    assert result["aggregate_oos"]["total_trades"] == 1
    assert round(result["aggregate_oos"]["total_return_pct"], 2) == 1.0


def test_evaluate_fixed_variant_walkforward_flags_negative_year_and_low_consistency():
    picks = pd.DataFrame(
        [
            {"Date": "2022-01-03", "week_key": "2022-W01", "code": "111111", "trade_return": 0.06},
            {"Date": "2022-01-03", "week_key": "2022-W01", "code": "222222", "trade_return": -0.02},
            {"Date": "2023-01-02", "week_key": "2023-W01", "code": "111111", "trade_return": -0.05},
            {"Date": "2023-01-02", "week_key": "2023-W01", "code": "222222", "trade_return": 0.03},
            {"Date": "2024-01-01", "week_key": "2024-W01", "code": "111111", "trade_return": 0.02},
            {"Date": "2024-01-01", "week_key": "2024-W01", "code": "222222", "trade_return": 0.01},
        ]
    )
    panel = pd.DataFrame(
        [
            {"Date": "2021-12-31", "code": "111111", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000000},
            {"Date": "2022-01-03", "code": "111111", "Open": 101, "High": 102, "Low": 100, "Close": 101, "Volume": 1000000},
            {"Date": "2022-12-30", "code": "111111", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000000},
            {"Date": "2023-01-02", "code": "111111", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000000},
            {"Date": "2023-12-29", "code": "111111", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000000},
            {"Date": "2024-01-01", "code": "111111", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000000},
            {"Date": "2021-12-31", "code": "222222", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},
            {"Date": "2022-01-03", "code": "222222", "Open": 102, "High": 110, "Low": 90, "Close": 102, "Volume": 1000},
            {"Date": "2022-12-30", "code": "222222", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},
            {"Date": "2023-01-02", "code": "222222", "Open": 102, "High": 110, "Low": 90, "Close": 102, "Volume": 1000},
            {"Date": "2023-12-29", "code": "222222", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000},
            {"Date": "2024-01-01", "code": "222222", "Open": 102, "High": 110, "Low": 90, "Close": 102, "Volume": 1000},
        ]
    )
    variant = {"variant_id": "baseline", "thresholds": None}

    result = evaluate_fixed_variant_walkforward(
        picks=picks,
        panel=panel,
        variant=variant,
        group_col="week_key",
        min_train_years=1,
    )

    assert result["aggregate_oos"]["periods"] == 2
    assert result["negative_test_years"] == 1
    assert result["positive_test_years"] == 1
    assert round(result["consistency_ratio"], 2) == 0.5


def test_walkforward_candidate_gate_rejects_negative_year_and_insufficient_consistency():
    gate = walkforward_candidate_gate(
        {
            "variant_id": "baseline",
            "aggregate_oos": {
                "total_return_pct": 5.0,
                "max_drawdown_pct": -15.0,
                "total_trades": 80,
            },
            "negative_test_years": 1,
            "positive_test_years": 2,
            "consistency_ratio": 2 / 3,
        },
        min_oos_trades=60,
        max_oos_drawdown_pct=-30.0,
        max_negative_years=0,
        min_consistency_ratio=0.75,
    )

    assert gate["approved"] is False
    assert "negative_test_years_exceeded" in gate["reasons"]
    assert "consistency_ratio_too_low" in gate["reasons"]
