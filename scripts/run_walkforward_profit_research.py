from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from toss_alpha.research.profit_loop import (
    branch_score,
    build_fast_veto_grid,
    evaluate_fixed_variant_walkforward,
    run_walkforward_variant_selection,
    walkforward_candidate_gate,
)

ROOT = Path(__file__).resolve().parents[1]
BACKTEST_DIR = ROOT / "reports" / "backtests"
REPORT_DIR = ROOT / "reports" / "harness"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

MONFRI_PICKS = BACKTEST_DIR / "random500_seed20260607_contextual_optimizer_mon_fri_cycle_2022-01-01_2025-12-31_combined_picks.csv"
PANEL_CSV = BACKTEST_DIR / "random500_seed20260607_2022-01-01_2025-12-31_ohlcv_panel.csv"
MIN_TRAIN_YEARS = 1
GATE_CONFIG = {
    "min_oos_trades": 60,
    "max_oos_drawdown_pct": -30.0,
    "max_negative_years": 0,
    "min_consistency_ratio": 0.67,
    "min_oos_total_return_pct": 0.0,
}


def fixed_variant_score(candidate: dict[str, Any]) -> float:
    perf = candidate["aggregate_oos"]
    return (
        float(perf.get("total_return_pct", 0.0))
        + 10.0 * float(perf.get("sharpe_proxy", 0.0))
        + float(perf.get("max_drawdown_pct", 0.0))
        - 12.0 * float(candidate.get("negative_test_years", 0))
        - 10.0 * max(0.0, 0.67 - float(candidate.get("consistency_ratio", 0.0)))
    )


def compact_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in candidate.items()
        if k not in {"aggregate_oos_kept_picks"}
    }


def main() -> None:
    monfri_picks = pd.read_csv(MONFRI_PICKS, dtype={"code": str})
    panel = pd.read_csv(PANEL_CSV, dtype={"code": str}, parse_dates=["Date"])

    variants = [{"variant_id": "baseline", "thresholds": None}] + build_fast_veto_grid()
    adaptive_result = run_walkforward_variant_selection(
        picks=monfri_picks,
        panel=panel,
        variants=variants,
        group_col="week_key",
        min_train_years=MIN_TRAIN_YEARS,
    )

    folds = adaptive_result["folds"]
    ranked_adaptive_folds = sorted(
        [
            {
                "variant_id": fold["selected_variant_id"],
                "performance": fold["test_performance"],
            }
            for fold in folds
        ],
        key=branch_score,
        reverse=True,
    )

    fixed_candidates = []
    for variant in variants:
        candidate = evaluate_fixed_variant_walkforward(
            picks=monfri_picks,
            panel=panel,
            variant=variant,
            group_col="week_key",
            min_train_years=MIN_TRAIN_YEARS,
        )
        gate = walkforward_candidate_gate(candidate, **GATE_CONFIG)
        candidate["gate"] = gate
        candidate["fixed_oos_score"] = round(fixed_variant_score(candidate), 3)
        fixed_candidates.append(candidate)
    fixed_candidates = sorted(fixed_candidates, key=lambda row: row["fixed_oos_score"], reverse=True)
    promotable_candidates = [row for row in fixed_candidates if row["gate"]["approved"]]
    best_fixed_candidate = fixed_candidates[0] if fixed_candidates else None
    recommended_fixed_candidate = promotable_candidates[0] if promotable_candidates else None

    generated_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"profit_research_walkforward_{generated_at}"
    json_path = REPORT_DIR / f"{stem}.json"
    md_path = REPORT_DIR / f"{stem}.md"

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "monfri_picks": str(MONFRI_PICKS),
            "panel_csv": str(PANEL_CSV),
            "variants": variants,
            "min_train_years": MIN_TRAIN_YEARS,
            "gate_config": GATE_CONFIG,
        },
        "adaptive_selection": {
            "folds": folds,
            "aggregate_oos": adaptive_result["aggregate_oos"],
            "selected_variant_frequency": pd.Series([fold["selected_variant_id"] for fold in folds]).value_counts().to_dict(),
            "best_oos_fold": ranked_adaptive_folds[0] if ranked_adaptive_folds else None,
        },
        "fixed_variant_leaderboard": [compact_candidate(row) for row in fixed_candidates],
        "recommended_fixed_candidate": compact_candidate(recommended_fixed_candidate) if recommended_fixed_candidate else None,
        "best_fixed_candidate_even_if_rejected": compact_candidate(best_fixed_candidate) if best_fixed_candidate else None,
        "promotable_candidate_count": len(promotable_candidates),
        "disclaimer": "Walk-forward out-of-sample revalidation on historical data only. Not investment advice. Live orders not submitted.",
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Profit research walk-forward report",
        "",
        "Research-only. 실주문 없음. 투자 조언 아님.",
        "",
        "## Adaptive train-winner OOS",
        f"- performance: {adaptive_result['aggregate_oos']}",
        f"- selected_variant_frequency: {payload['adaptive_selection']['selected_variant_frequency']}",
        "",
        "## Fixed variant leaderboard",
    ]
    for row in fixed_candidates:
        lines.extend(
            [
                f"- variant_id: {row['variant_id']}",
                f"  - fixed_oos_score: {row['fixed_oos_score']}",
                f"  - aggregate_oos: {row['aggregate_oos']}",
                f"  - negative_test_years: {row['negative_test_years']}",
                f"  - positive_test_years: {row['positive_test_years']}",
                f"  - consistency_ratio: {round(float(row['consistency_ratio']), 3)}",
                f"  - gate: {row['gate']}",
            ]
        )
    lines.extend([
        "",
        "## Adaptive fold details",
    ])
    for fold in folds:
        lines.extend(
            [
                f"- test_year: {fold['test_year']}",
                f"  - train_years: {fold['train_years']}",
                f"  - selected_variant_id: {fold['selected_variant_id']}",
                f"  - selected_train_score: {fold['selected_train_score']}",
                f"  - train_performance: {fold['train_performance']}",
                f"  - test_performance: {fold['test_performance']}",
                f"  - test_kept_trades: {fold['test_kept_trades']}",
                f"  - test_blocked_trades: {fold['test_blocked_trades']}",
                f"  - test_blocked_counts_by_reason: {fold['test_blocked_counts_by_reason']}",
            ]
        )
    lines.extend(
        [
            "",
            "## Verdict guide",
            "- adaptive OOS가 양수여도 fixed candidate gate를 못 넘으면 단일 threshold 승격은 보류한다.",
            "- promotable fixed candidate가 1개 이상일 때만 generated policy 고정 승격 후보로 간주한다.",
            "",
            "## Outputs",
            f"- json: {json_path}",
            f"- md: {md_path}",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"REPORT_JSON={json_path}")
    print(f"REPORT_MD={md_path}")
    print(f"ADAPTIVE_OOS_TOTAL_RETURN_PCT={adaptive_result['aggregate_oos']['total_return_pct']}")
    print(f"ADAPTIVE_OOS_MAX_DRAWDOWN_PCT={adaptive_result['aggregate_oos']['max_drawdown_pct']}")
    print(f"ADAPTIVE_OOS_TOTAL_TRADES={adaptive_result['aggregate_oos']['total_trades']}")
    print(f"PROMOTABLE_FIXED_CANDIDATES={len(promotable_candidates)}")
    if recommended_fixed_candidate:
        print(f"RECOMMENDED_FIXED_VARIANT={recommended_fixed_candidate['variant_id']}")
        print(f"RECOMMENDED_FIXED_VARIANT_OOS_RETURN_PCT={recommended_fixed_candidate['aggregate_oos']['total_return_pct']}")


if __name__ == "__main__":
    main()
