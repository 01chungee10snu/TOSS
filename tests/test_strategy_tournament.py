from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "strategy_tournament.py"


def load_module():
    spec = importlib.util.spec_from_file_location("strategy_tournament_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def seed_tournament_reports(m, tmp_path: Path) -> None:
    m.ROOT = tmp_path
    m.REPORTS = tmp_path / "reports"
    m.VALIDATION = m.REPORTS / "validation"
    m.VALIDATION.mkdir(parents=True, exist_ok=True)

    _write_json(
        m.VALIDATION / "executable_etf_portfolio_latest.json",
        {
            "strategies": {
                "kodex200_60_bondplus_40": {"069500": 0.6, "214980": 0.4},
                "kodex_kospi_50_bond_50": {"226490": 0.5, "153130": 0.5},
            },
            "promotions": {
                "kodex200_60_bondplus_40": {
                    "paper_candidate_passed": True,
                    "live_promotion_passed": False,
                    "live_block_reason": "forward_paper_and_orderbook_depth_evidence_missing",
                    "positive_year_share": 0.67,
                    "stress_75bp": {
                        "cagr_pct": 8.45,
                        "total_return_pct": 152.98,
                        "sharpe_zero_rf": 0.8488,
                        "max_drawdown_pct": -17.98,
                        "per_trade_notional_cost_bps": 75,
                        "rebalances": 138,
                    },
                },
                "kodex_kospi_50_bond_50": {
                    "paper_candidate_passed": True,
                    "live_promotion_passed": False,
                    "live_block_reason": "forward_paper_and_orderbook_depth_evidence_missing",
                    "positive_year_share": 0.75,
                    "stress_75bp": {
                        "cagr_pct": 8.16,
                        "total_return_pct": 136.28,
                        "sharpe_zero_rf": 0.8633,
                        "max_drawdown_pct": -19.52,
                        "per_trade_notional_cost_bps": 75,
                        "rebalances": 133,
                    },
                },
            },
        },
    )
    _write_json(
        m.REPORTS / "harness" / "executable_etf_paper_latest.json",
        {
            "target_weights": {"226490": 0.5, "153130": 0.5},
            "forward_paper_gate": {"passed": False},
        },
    )
    _write_json(
        m.VALIDATION / "pit_validation_20260812T044559Z.json",
        {
            "walkforward_results": [
                {
                    "strategy": "reversal_oversold",
                    "cost_bps": 31,
                    "oos_windows": 14,
                    "positive_windows": 1,
                    "avg_oos_return_pct": -13.4,
                    "avg_oos_sharpe": -2.4679,
                    "total_oos_trades": 524,
                    "windows": [{"max_drawdown_pct": -20.0}],
                }
            ]
        },
    )
    _write_json(
        m.VALIDATION / "hml_cma_quarterly_v2_20260826T122735Z.json",
        {
            "data_quality": {"survivorship_bias_resolved": False},
            "hml_cma_composite": {
                "75bp": {
                    "cagr_pct": 67.75,
                    "total_return_pct": 100.97,
                    "sharpe_ratio": 1.656,
                    "max_drawdown_pct": -27.66,
                    "cost_bps": 75,
                    "rebalances": 6,
                    "trading_days": 340,
                    "yearly_returns_pct": {"2025": 58.41, "2026": 28.07},
                }
            },
        },
    )
    _write_json(
        m.VALIDATION / "executable_etf_trend_sleeve_latest.json",
        {
            "selected_train_only_variant": "dual60_200_50_10",
            "selection_contract": {"selection_cost_stress_bps": 75},
            "results": [
                {
                    "variant": "dual60_200_50_10",
                    "cost_bps": 75,
                    "holdout": {
                        "days": 1104,
                        "total_return_pct": 57.94,
                        "cagr_pct": 10.44,
                        "sharpe": 0.9348,
                        "max_drawdown_pct": -17.95,
                    },
                }
            ],
            "selected_vs_static_50_50_correlation": {
                "holdout": {"pearson": 0.9413, "downside": 0.9233}
            },
            "static_50_50_holdout_metrics_at_selection_cost": {
                "total_return_pct": 68.02,
                "sharpe": 0.9465,
            },
            "holdout_independent_alpha_gate": {
                "passed": False,
                "reasons": [
                    "insufficient_independence_from_static_etf_sleeve",
                    "does_not_outperform_static_50_50_return",
                ],
            },
        },
    )
    _write_json(
        m.REPORTS / "backtests" / "breakout_ensemble_v5_pit.json",
        {
            "best_overall_train_selected": "ensemble_consensus3|h10|k20",
            "details": {
                "ensemble_consensus3|h10|k20": {
                    "train": {
                        "episodes": 67,
                        "cumulative_return": -0.6158,
                        "sharpe": -1.2807,
                        "max_drawdown": -0.6601,
                    },
                    "test": {
                        "episodes": 36,
                        "cumulative_return": -0.3295,
                        "sharpe": -0.6562,
                        "max_drawdown": -0.4361,
                    },
                }
            },
            "promotion": {
                "verdict": "BLOCKED_RESEARCH_ONLY_UNRESOLVED_PIT_AND_CORPORATE_ACTIONS"
            },
        },
    )
    _write_json(
        m.VALIDATION / "contextual_train_only_holdout_latest.json",
        {
            "daily_contextual": {
                "holdout": {
                    "cagr_pct": -40.76,
                    "total_return_pct": -40.48,
                    "sharpe": -1.96,
                    "max_drawdown_pct": -47.65,
                    "total_trades": 213,
                },
                "holdout_verdict": {"passed": False, "reasons": ["non_positive_holdout_return"]},
            },
            "monfri_contextual": {
                "holdout": {
                    "cagr_pct": -3.06,
                    "total_return_pct": -2.99,
                    "sharpe": -0.084,
                    "max_drawdown_pct": -12.55,
                    "total_trades": 173,
                },
                "holdout_verdict": {"passed": False, "reasons": ["non_positive_holdout_return"]},
            },
        },
    )
    _write_json(
        m.REPORTS / "harness" / "backtest_current_live_strategy.json",
        {
            "config": {"initial_capital_krw": 1_000_000, "round_trip_bps": 31},
            "summary": {
                "total_pnl_krw": -3_000_000,
                "sharpe": -1.2,
                "profit_factor": 0.6,
                "max_drawdown_pct": -100.0,
                "capital_exhausted": True,
                "total_trades": 3000,
            },
        },
    )


def test_performance_score_rewards_better_risk_adjusted_profile():
    m = load_module()
    better = m.performance_score(cagr_pct=10.0, sharpe=1.0, max_drawdown_pct=-15.0)
    worse = m.performance_score(cagr_pct=3.0, sharpe=0.2, max_drawdown_pct=-30.0)
    assert better > worse


def test_sort_prioritizes_promotion_state_before_raw_performance():
    m = load_module()
    paper = m.candidate(
        strategy_id="paper",
        family="x",
        status="PAPER_CANDIDATE",
        evidence_grade="B",
        source="x",
        cagr_pct=8.0,
        sharpe=0.8,
        max_drawdown_pct=-20.0,
    )
    flashy_research = m.candidate(
        strategy_id="flashy",
        family="y",
        status="RESEARCH_ONLY",
        evidence_grade="D",
        source="y",
        cagr_pct=70.0,
        sharpe=1.7,
        max_drawdown_pct=-25.0,
    )
    ranked = m.sort_candidates([flashy_research, paper])
    assert ranked[0]["strategy_id"] == "paper"


def test_repository_tournament_keeps_live_closed_and_etf_on_top(tmp_path):
    m = load_module()
    seed_tournament_reports(m, tmp_path)
    report = m.build_tournament()

    assert report["decision"] == "NO_NEW_LIVE_PROMOTION"
    assert report["live_eligible_count"] == 0
    assert report["leaderboard"][0]["family"] == "executable_etf"
    assert report["paper_candidate_count"] >= 1

    by_id = {row["strategy_id"]: row for row in report["leaderboard"]}
    assert by_id["current_live_strategy"]["status"] == "REJECTED"
    assert by_id["reversal_oversold"]["status"] == "REJECTED"
    assert by_id["hml_cma_composite"]["status"] == "RESEARCH_ONLY"
    assert by_id["hml_cma_composite"]["evidence_grade"] == "D"
    assert by_id["contextual_daily_train_only_holdout"]["status"] == "REJECTED"
    assert by_id["contextual_monfri_train_only_holdout"]["status"] == "REJECTED"
    assert by_id["contextual_daily_train_only_holdout"]["evidence_grade"] == "C"
    assert by_id["breakout_ensemble_v5_corrected"]["status"] == "REJECTED"
    assert by_id["breakout_ensemble_v5_corrected"]["evidence_grade"] == "C"
    assert by_id["breakout_ensemble_v5_corrected"]["total_return_pct"] < 0
    assert any("older v2/v3 optimistic results are superseded" in note for note in by_id["breakout_ensemble_v5_corrected"]["notes"])
    assert by_id["executable_etf_trend_diversifier"]["status"] == "REJECTED"
    assert by_id["executable_etf_trend_diversifier"]["evidence_grade"] == "B"
    assert any("independent_alpha_gate=False" in note for note in by_id["executable_etf_trend_diversifier"]["notes"])


def test_strict_true_pit_domestic_factors_supersede_legacy_naver_factor_report(tmp_path):
    m = load_module()
    seed_tournament_reports(m, tmp_path)
    _write_json(
        m.VALIDATION / "hml_cma_true_pit_latest.json",
        {
            "pit_contract": {"eligible": True, "status": "TRUE_PIT_ELIGIBLE"},
            "strategy_asof_coverage": {"passed": True},
            "profitability_diagnostic_75bp": {
                "passed_directional_factor_check": True,
                "high_minus_all": {"relative_sharpe": 0.5},
                "high_minus_low": {"relative_sharpe": 1.0},
            },
            "results": [
                {
                    "strategy": "hml_only",
                    "cost_bps": 75,
                    "cagr_pct": 9.1,
                    "total_return_pct": 42.0,
                    "sharpe_ratio": 0.9,
                    "max_drawdown_pct": -18.0,
                    "rebalances": 12,
                    "positive_year_share": 0.75,
                },
                {
                    "strategy": "profitability_proxy",
                    "cost_bps": 75,
                    "cagr_pct": 7.5,
                    "total_return_pct": 33.0,
                    "sharpe_ratio": 0.8,
                    "max_drawdown_pct": -16.0,
                    "rebalances": 12,
                    "positive_year_share": 0.75,
                },
            ],
        },
    )

    report = m.build_tournament()
    by_id = {row["strategy_id"]: row for row in report["leaderboard"]}

    assert by_id["hml_only"]["family"] == "true_pit_domestic_factor"
    assert by_id["hml_only"]["evidence_grade"] == "C"
    assert by_id["hml_only"]["source"] == "reports/validation/hml_cma_true_pit_latest.json"
    assert by_id["profitability_proxy"]["status"] == "RESEARCH_ONLY"
    assert by_id["hml_cma_composite"]["family"] == "hml_cma_factor"
    assert by_id["hml_cma_composite"]["evidence_grade"] == "D"


def test_current_forward_paper_target_is_identified_by_weights(tmp_path):
    m = load_module()
    seed_tournament_reports(m, tmp_path)
    report = m.build_tournament()
    row = next(x for x in report["leaderboard"] if x["strategy_id"] == "kodex_kospi_50_bond_50")
    assert any("current forward-paper target" in note for note in row["notes"])
