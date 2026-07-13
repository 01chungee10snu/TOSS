from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from toss_alpha.research.profit_loop import (
    apply_extra_cost_bps,
    evaluate_fixed_variant_walkforward,
    walkforward_candidate_gate,
)

ROOT = Path(__file__).resolve().parents[1]
PICKS_CSV = ROOT / "reports/harness/entry_gap_veto_frozen_policy_picks_2022_2026.csv"
PANEL_CSV = ROOT / "reports/backtests/random500_seed20260607_2022-01-01_2026-latest_ohlcv_panel.csv"
REPORT_DIR = ROOT / "reports/harness"
COST_STRESS_BPS = [0.0, 10.0, 20.0, 30.0]
GATE_CONFIG = {
    "min_oos_trades": 60,
    "max_oos_drawdown_pct": -30.0,
    "max_negative_years": 0,
    "min_consistency_ratio": 0.67,
    "min_oos_total_return_pct": 0.0,
}

VARIANTS: list[dict[str, Any]] = [
    {"variant_id": "baseline", "thresholds": None},
    {
        "variant_id": "leak_free_current_equivalent",
        "thresholds": {
            "max_gap_pct": 0.08,
            "max_intraday_range_pct": 0.22,
            "min_dollar_volume_krw": 1_000_000_000.0,
            "max_prev_volatility_20d": 0.10,
            "min_tail_risk_flags": 1,
        },
    },
    {
        "variant_id": "two_factor_moderate",
        "thresholds": {
            "max_gap_pct": 0.06,
            "max_intraday_range_pct": 0.08,
            "min_dollar_volume_krw": 1_000_000_000.0,
            "max_prev_volatility_20d": 0.06,
            "max_prev_volume_surge_20d": 3.0,
            "min_tail_risk_flags": 2,
        },
    },
    {
        "variant_id": "two_factor_strict",
        "thresholds": {
            "max_gap_pct": 0.05,
            "max_intraday_range_pct": 0.06,
            "min_dollar_volume_krw": 1_000_000_000.0,
            "max_prev_volatility_20d": 0.05,
            "max_prev_volume_surge_20d": 2.0,
            "min_tail_risk_flags": 2,
        },
    },
]


def compact(candidate: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in candidate.items() if key != "aggregate_oos_kept_picks"}


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    picks = pd.read_csv(PICKS_CSV, dtype={"code": str}, parse_dates=["Date"])
    panel = pd.read_csv(PANEL_CSV, dtype={"code": str}, parse_dates=["Date"])
    yearly_trade_counts = {str(int(year)): int(count) for year, count in picks.groupby(picks["Date"].dt.year).size().items()}
    holdout_2026_trades = int((picks["Date"].dt.year == 2026).sum())

    bundles: list[dict[str, Any]] = []
    for variant in VARIANTS:
        scenarios = []
        for extra_bps in COST_STRESS_BPS:
            stressed = apply_extra_cost_bps(picks, extra_round_trip_bps=extra_bps)
            candidate = evaluate_fixed_variant_walkforward(
                picks=stressed,
                panel=panel,
                variant=variant,
                group_col="week_key",
                min_train_years=1,
            )
            gate = walkforward_candidate_gate(candidate, **GATE_CONFIG)
            scenarios.append({
                "extra_round_trip_bps": extra_bps,
                "candidate": compact(candidate),
                "gate": gate,
            })
        base = scenarios[0]["candidate"]
        worst_return = min(float(row["candidate"]["aggregate_oos"]["total_return_pct"]) for row in scenarios)
        worst_mdd = min(float(row["candidate"]["aggregate_oos"]["max_drawdown_pct"]) for row in scenarios)
        bundles.append({
            "variant_id": variant["variant_id"],
            "thresholds": variant.get("thresholds"),
            "base_oos": base["aggregate_oos"],
            "base_negative_test_years": base["negative_test_years"],
            "base_consistency_ratio": base["consistency_ratio"],
            "all_stress_gates_pass": all(row["gate"]["approved"] for row in scenarios),
            "worst_case_return_pct": worst_return,
            "worst_case_drawdown_pct": worst_mdd,
            "scenarios": scenarios,
        })

    baseline = bundles[0]
    for bundle in bundles:
        bundle["return_delta_vs_baseline_pct"] = round(
            float(bundle["base_oos"]["total_return_pct"]) - float(baseline["base_oos"]["total_return_pct"]), 2
        )
        bundle["mdd_delta_vs_baseline_pct"] = round(
            float(bundle["base_oos"]["max_drawdown_pct"]) - float(baseline["base_oos"]["max_drawdown_pct"]), 2
        )
        bundle["promotion_eligible"] = bool(
            bundle["variant_id"] != "baseline"
            and bundle["all_stress_gates_pass"]
            and bundle["return_delta_vs_baseline_pct"] >= 0.0
            and bundle["mdd_delta_vs_baseline_pct"] >= 0.0
            and holdout_2026_trades > 0
        )

    eligible = [row for row in bundles if row["promotion_eligible"]]
    verdict = "PROMOTE" if eligible else "REJECT_NO_POLICY_CHANGE"
    reasons = []
    if holdout_2026_trades == 0:
        reasons.append("frozen_mon_fri_policy_has_zero_2026_trades_no_2026_holdout_evidence")
    if not eligible:
        reasons.append("no_candidate_met_strict_return_drawdown_stress_and_holdout_gate")

    generated_at = datetime.now(timezone.utc)
    stem = f"entry_gap_veto_research_{generated_at.strftime('%Y%m%dT%H%M%SZ')}"
    json_path = REPORT_DIR / f"{stem}.json"
    md_path = REPORT_DIR / f"{stem}.md"
    payload = {
        "generated_at_utc": generated_at.isoformat(),
        "research_only": True,
        "live_order_submitted": False,
        "policy_written": False,
        "inputs": {"picks_csv": str(PICKS_CSV), "panel_csv": str(PANEL_CSV)},
        "method": {
            "entry": "Monday open",
            "exit": "same-week Friday close",
            "leakage_rule": "opening gap is contemporaneously available; range/dollar-volume/volume-surge/volatility are lagged one trading session",
            "oos_years": [2023, 2024, 2025],
            "cost_stress_extra_round_trip_bps": COST_STRESS_BPS,
            "gate_config": GATE_CONFIG,
        },
        "yearly_trade_counts": yearly_trade_counts,
        "holdout_2026_trades": holdout_2026_trades,
        "variants": bundles,
        "verdict": verdict,
        "verdict_reasons": reasons,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = []
    for bundle in bundles:
        rows.append({
            "variant": bundle["variant_id"],
            "oos_return_pct": bundle["base_oos"]["total_return_pct"],
            "oos_mdd_pct": bundle["base_oos"]["max_drawdown_pct"],
            "oos_trades": bundle["base_oos"]["total_trades"],
            "negative_years": bundle["base_negative_test_years"],
            "worst_cost_return_pct": bundle["worst_case_return_pct"],
            "all_stress_pass": bundle["all_stress_gates_pass"],
            "promotion_eligible": bundle["promotion_eligible"],
        })
    table = pd.DataFrame(rows).to_markdown(index=False)
    md_path.write_text(
        "\n".join([
            "# Entry gap/tail-risk veto research",
            "",
            "Research only. No live orders or policy writes.",
            "",
            f"- Verdict: **{verdict}**",
            f"- 2026 frozen-policy holdout trades: {holdout_2026_trades}",
            f"- Reasons: {', '.join(reasons)}",
            "- Leakage control: Monday gap only from entry session; all other veto features lagged one trading day.",
            "",
            "## Results",
            table,
            "",
            f"- JSON: `{json_path}`",
        ]) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"verdict": verdict, "reasons": reasons, "results": rows, "json": str(json_path), "markdown": str(md_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
