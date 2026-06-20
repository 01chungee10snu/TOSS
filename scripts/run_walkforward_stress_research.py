from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from toss_alpha.research.profit_loop import (
    apply_extra_cost_bps,
    build_fast_veto_grid,
    build_profit_snapshot,
    evaluate_fixed_variant_walkforward,
    walkforward_candidate_gate,
)

ROOT = Path(__file__).resolve().parents[1]
BACKTEST_DIR = ROOT / "reports" / "backtests"
REPORT_DIR = ROOT / "reports" / "harness"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
POLICY_DIR = ROOT / "config" / "generated_policies"

MONFRI_PICKS = BACKTEST_DIR / "random500_seed20260607_contextual_optimizer_mon_fri_cycle_2022-01-01_2025-12-31_combined_picks.csv"
PANEL_CSV = BACKTEST_DIR / "random500_seed20260607_2022-01-01_2025-12-31_ohlcv_panel.csv"
BASE_POLICY_JSON = POLICY_DIR / "contextual_mon_fri_policy_seed20260607.json"
PROMOTED_POLICY_JSON = POLICY_DIR / "contextual_mon_fri_policy_seed20260607_walkforward_promoted.json"
MIN_TRAIN_YEARS = 1
GATE_CONFIG = {
    "min_oos_trades": 60,
    "max_oos_drawdown_pct": -30.0,
    "max_negative_years": 0,
    "min_consistency_ratio": 0.67,
    "min_oos_total_return_pct": 0.0,
}
STRESS_SCENARIOS = [
    {"scenario_id": "base", "extra_round_trip_bps": 0.0},
    {"scenario_id": "stress_plus_10bps", "extra_round_trip_bps": 10.0},
    {"scenario_id": "stress_plus_20bps", "extra_round_trip_bps": 20.0},
    {"scenario_id": "stress_plus_30bps", "extra_round_trip_bps": 30.0},
]


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
    return {k: v for k, v in candidate.items() if k not in {"aggregate_oos_kept_picks"}}


def scenario_summary(*, scenario_id: str, extra_round_trip_bps: float, candidate: dict[str, Any]) -> dict[str, Any]:
    gate = walkforward_candidate_gate(candidate, **GATE_CONFIG)
    payload = compact_candidate(candidate)
    payload["scenario_id"] = scenario_id
    payload["extra_round_trip_bps"] = extra_round_trip_bps
    payload["gate"] = gate
    payload["fixed_oos_score"] = round(fixed_variant_score(candidate), 3)
    return payload


def robust_candidate_score(bundle: dict[str, Any]) -> float:
    scenarios = bundle["scenarios"]
    approved_count = sum(1 for row in scenarios if row["gate"]["approved"])
    worst_return = min(float(row["aggregate_oos"].get("total_return_pct", 0.0)) for row in scenarios)
    worst_mdd = min(float(row["aggregate_oos"].get("max_drawdown_pct", 0.0)) for row in scenarios)
    worst_sharpe = min(float(row["aggregate_oos"].get("sharpe_proxy", 0.0)) for row in scenarios)
    return round(worst_return + 10.0 * worst_sharpe + worst_mdd + 5.0 * approved_count, 3)


def build_promoted_policy(base_policy: dict[str, Any], robust_winner: dict[str, Any], report_json: Path) -> dict[str, Any]:
    winner_base = robust_winner["base_scenario"]
    return {
        **base_policy,
        "policy_id": "contextual_mon_fri_policy_seed20260607_walkforward_promoted",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_policy_source": str(BASE_POLICY_JSON),
        "walkforward_report_source": str(report_json),
        "fast_veto_overlay": {
            "variant_id": winner_base["variant_id"],
            "thresholds": winner_base.get("thresholds"),
            "application": "apply these Monday-entry fast-veto thresholds before accepting a candidate from the base contextual mon-fri policy",
        },
        "walkforward_validation": {
            "gate_config": GATE_CONFIG,
            "recommended_fixed_candidate": winner_base,
            "promotable_candidate_count": int(robust_winner["base_gate_approved_count"]),
        },
        "stress_validation": {
            "scenarios": STRESS_SCENARIOS,
            "robust_winner": robust_winner,
            "criterion": "winner must pass gate in every configured stress scenario; ranking uses worst-case return/sharpe/drawdown plus approved-scenario count",
        },
        "promotion_verdict": "PROMOTED_FOR_NEXT_REPLAY",
        "disclaimer": "Research-only promoted Monday-buy Friday-sell policy with walk-forward-approved fast-veto overlay and cost stress validation. Not investment advice. Live orders not submitted.",
    }


def main() -> None:
    monfri_picks = pd.read_csv(MONFRI_PICKS, dtype={"code": str})
    panel = pd.read_csv(PANEL_CSV, dtype={"code": str}, parse_dates=["Date"])
    base_policy = json.loads(BASE_POLICY_JSON.read_text(encoding="utf-8"))

    variants = [{"variant_id": "baseline", "thresholds": None}] + build_fast_veto_grid()
    bundles = []
    for variant in variants:
        scenario_rows = []
        for scenario in STRESS_SCENARIOS:
            stressed_picks = apply_extra_cost_bps(monfri_picks, extra_round_trip_bps=float(scenario["extra_round_trip_bps"]))
            candidate = evaluate_fixed_variant_walkforward(
                picks=stressed_picks,
                panel=panel,
                variant=variant,
                group_col="week_key",
                min_train_years=MIN_TRAIN_YEARS,
            )
            scenario_rows.append(
                scenario_summary(
                    scenario_id=str(scenario["scenario_id"]),
                    extra_round_trip_bps=float(scenario["extra_round_trip_bps"]),
                    candidate=candidate,
                )
            )
        bundle = {
            "variant_id": variant["variant_id"],
            "thresholds": variant.get("thresholds"),
            "scenarios": scenario_rows,
            "base_scenario": scenario_rows[0],
            "base_gate_approved_count": sum(1 for row in scenario_rows if row["scenario_id"] == "base" and row["gate"]["approved"]),
            "stress_pass_count": sum(1 for row in scenario_rows if row["gate"]["approved"]),
        }
        bundle["all_scenarios_approved"] = bundle["stress_pass_count"] == len(STRESS_SCENARIOS)
        bundle["robust_score"] = robust_candidate_score(bundle)
        bundle["worst_case_return_pct"] = min(float(row["aggregate_oos"].get("total_return_pct", 0.0)) for row in scenario_rows)
        bundle["worst_case_drawdown_pct"] = min(float(row["aggregate_oos"].get("max_drawdown_pct", 0.0)) for row in scenario_rows)
        bundle["worst_case_mdd_pct"] = bundle["worst_case_drawdown_pct"]
        bundle["worst_case_scenario_id"] = min(
            scenario_rows,
            key=lambda r: float(r["aggregate_oos"].get("total_return_pct", 0.0)),
        )["scenario_id"]
        bundle["worst_case_sharpe_proxy"] = min(
            float(row["aggregate_oos"].get("sharpe_proxy", 0.0)) for row in scenario_rows
        )
        bundles.append(bundle)

    bundles = sorted(
        bundles,
        key=lambda row: (row["all_scenarios_approved"], row["robust_score"], row["worst_case_return_pct"]),
        reverse=True,
    )
    robust_winners = [row for row in bundles if row["all_scenarios_approved"]]
    robust_winner = robust_winners[0] if robust_winners else None
    runner_up = bundles[1] if len(bundles) > 1 else None

    generated_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"profit_research_walkforward_stress_{generated_at}"
    json_path = REPORT_DIR / f"{stem}.json"
    md_path = REPORT_DIR / f"{stem}.md"

    promoted_policy = None
    if robust_winner and robust_winner["base_scenario"]["gate"]["approved"] and robust_winner["thresholds"] is not None:
        promoted_policy = build_promoted_policy(base_policy, robust_winner, json_path)
        PROMOTED_POLICY_JSON.write_text(json.dumps(promoted_policy, ensure_ascii=False, indent=2), encoding="utf-8")

    generated_at_utc = datetime.now(timezone.utc).isoformat()
    snapshot = build_profit_snapshot(
        robust_winner=robust_winner,
        runner_up=runner_up,
        generated_at_utc=generated_at_utc,
        stress_report_md=str(md_path),
        stress_report_json=str(json_path),
        promoted_policy_json=str(PROMOTED_POLICY_JSON) if promoted_policy is not None else "",
    )

    payload = {
        "generated_at_utc": generated_at_utc,
        "inputs": {
            "monfri_picks": str(MONFRI_PICKS),
            "panel_csv": str(PANEL_CSV),
            "base_policy_json": str(BASE_POLICY_JSON),
            "promoted_policy_json": str(PROMOTED_POLICY_JSON),
            "gate_config": GATE_CONFIG,
            "stress_scenarios": STRESS_SCENARIOS,
        },
        "variant_stress_leaderboard": bundles,
        "robust_winner": robust_winner,
        "runner_up": runner_up,
        "robust_winner_count": len(robust_winners),
        "promoted_policy_written": promoted_policy is not None,
        "snapshot": snapshot,
        "disclaimer": "Walk-forward fixed-variant stress revalidation on historical data only. Not investment advice. Live orders not submitted.",
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Profit research walk-forward stress report",
        "",
        "Research-only. 실주문 없음. 투자 조언 아님.",
        "",
        f"- stress_scenarios: {STRESS_SCENARIOS}",
        f"- robust_winner_count: {len(robust_winners)}",
        f"- promoted_policy_written: {promoted_policy is not None}",
        "",
        "## Robust winner",
    ]
    if robust_winner:
        lines.extend([
            f"- variant_id: {robust_winner['variant_id']}",
            f"- robust_score: {robust_winner['robust_score']}",
            f"- stress_pass_count: {robust_winner['stress_pass_count']}/{len(STRESS_SCENARIOS)}",
            f"- worst_case_return_pct: {robust_winner['worst_case_return_pct']}",
            f"- worst_case_drawdown_pct: {robust_winner['worst_case_drawdown_pct']}",
            f"- thresholds: {robust_winner['thresholds']}",
        ])
        for row in robust_winner["scenarios"]:
            lines.append(
                f"  - {row['scenario_id']}: return {row['aggregate_oos']['total_return_pct']}%, MDD {row['aggregate_oos']['max_drawdown_pct']}%, trades {row['aggregate_oos']['total_trades']}, gate {row['gate']['approved']}"
            )
    else:
        lines.append("- none: no variant passed all configured stress scenarios")
    lines.extend(["", "## Top variants"])
    for row in bundles[:5]:
        lines.append(
            f"- {row['variant_id']}: all_scenarios_approved={row['all_scenarios_approved']}, robust_score={row['robust_score']}, worst_case_return={row['worst_case_return_pct']}%, worst_case_drawdown={row['worst_case_drawdown_pct']}%"
        )
    lines.extend([
        "",
        "## Outputs",
        f"- json: {json_path}",
        f"- md: {md_path}",
        f"- promoted_policy_json: {PROMOTED_POLICY_JSON}",
    ])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"REPORT_JSON={json_path}")
    print(f"REPORT_MD={md_path}")
    print(f"ROBUST_WINNER_COUNT={len(robust_winners)}")
    print(f"ALERT_LEVEL={snapshot['alert_level']}")
    if runner_up:
        print(f"RUNNER_UP_VARIANT={runner_up['variant_id']}")
    if robust_winner:
        print(f"ROBUST_WINNER_VARIANT={robust_winner['variant_id']}")
        print(f"ROBUST_WINNER_WORST_CASE_RETURN_PCT={robust_winner['worst_case_return_pct']}")
        print(f"PROMOTED_POLICY_WRITTEN={promoted_policy is not None}")


if __name__ == "__main__":
    main()
