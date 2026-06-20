from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from toss_alpha.research.profit_loop import (
    branch_score,
    build_fast_veto_grid,
    choose_best_branch,
    evaluate_fast_veto_variant,
    load_json,
)

ROOT = Path(__file__).resolve().parents[1]
BACKTEST_DIR = ROOT / "reports" / "backtests"
REPORT_DIR = ROOT / "reports" / "harness"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

DAILY_JSON = BACKTEST_DIR / "random500_seed20260607_contextual_optimizer_2022-01-01_2025-12-31.json"
MONFRI_JSON = BACKTEST_DIR / "random500_seed20260607_contextual_optimizer_mon_fri_cycle_2022-01-01_2025-12-31.json"
MONFRI_PICKS = BACKTEST_DIR / "random500_seed20260607_contextual_optimizer_mon_fri_cycle_2022-01-01_2025-12-31_combined_picks.csv"
PANEL_CSV = BACKTEST_DIR / "random500_seed20260607_2022-01-01_2025-12-31_ohlcv_panel.csv"


def branch_from_baseline(*, branch_id: str, cycle: str, method: str, perf: dict[str, Any], source_path: Path) -> dict[str, Any]:
    return {
        "branch_id": branch_id,
        "cycle": cycle,
        "method": method,
        "source_path": str(source_path),
        "performance": {
            "periods": int(perf.get("active_days", perf.get("days", 0))),
            "total_trades": int(perf.get("total_trades", 0)),
            "total_return_pct": float(perf.get("total_return_pct", 0.0)),
            "max_drawdown_pct": float(perf.get("max_drawdown_pct", 0.0)),
            "win_rate_pct": float(perf.get("win_rate_pct", 0.0)),
            "sharpe_proxy": float(perf.get("sharpe", 0.0)),
        },
    }


def main() -> None:
    daily_payload = load_json(DAILY_JSON)
    monfri_payload = load_json(MONFRI_JSON)
    monfri_picks = pd.read_csv(MONFRI_PICKS, dtype={"code": str})
    panel = pd.read_csv(PANEL_CSV, dtype={"code": str}, parse_dates=["Date"])

    branches: list[dict[str, Any]] = [
        branch_from_baseline(
            branch_id="daily_contextual_baseline",
            cycle="daily",
            method="baseline",
            perf=daily_payload["combined_all"],
            source_path=DAILY_JSON,
        ),
        branch_from_baseline(
            branch_id="monfri_contextual_baseline",
            cycle="monfri",
            method="baseline",
            perf=monfri_payload["combined_all"],
            source_path=MONFRI_JSON,
        ),
    ]

    veto_variants = []
    for variant in build_fast_veto_grid():
        result = evaluate_fast_veto_variant(
            picks=monfri_picks,
            panel=panel,
            thresholds=variant["thresholds"],
            group_col="week_key",
        )
        veto_variants.append(
            {
                "branch_id": f"monfri_{variant['variant_id']}",
                "cycle": "monfri",
                "method": "fast_veto_frontier",
                "source_path": str(MONFRI_PICKS),
                "thresholds": variant["thresholds"],
                "blocked_trades": result["blocked_trades"],
                "kept_trades": result["kept_trades"],
                "blocked_counts_by_reason": result["blocked_counts_by_reason"],
                "performance": result["performance"],
            }
        )
    branches.extend(veto_variants)

    ranked = sorted(branches, key=branch_score, reverse=True)
    best = choose_best_branch(branches)

    generated_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"profit_research_loop_{generated_at}"
    json_path = REPORT_DIR / f"{stem}.json"
    md_path = REPORT_DIR / f"{stem}.md"

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "daily_json": str(DAILY_JSON),
            "monfri_json": str(MONFRI_JSON),
            "monfri_picks": str(MONFRI_PICKS),
            "panel_csv": str(PANEL_CSV),
        },
        "branches": branches,
        "best_branch": best,
        "top3": ranked[:3],
        "disclaimer": "Research-only profitability exploration on historical data. Not investment advice. Live orders not submitted.",
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Profit research loop report",
        "",
        "Research-only. 실주문 없음. 투자 조언 아님.",
        "",
        "## Best branch",
        f"- branch_id: {best['branch_id']}",
        f"- cycle: {best['cycle']}",
        f"- method: {best['method']}",
        f"- recommendation: {best['recommendation']}",
        f"- score: {best['score']}",
        f"- performance: {best['performance']}",
    ]
    if best.get("thresholds"):
        lines.append(f"- thresholds: {best['thresholds']}")
    lines.extend([
        "",
        "## Top branches",
    ])
    for row in ranked[:5]:
        lines.append(
            f"- {row['branch_id']}: return {row['performance']['total_return_pct']}%, "
            f"MDD {row['performance']['max_drawdown_pct']}%, "
            f"SharpeProxy {row['performance']['sharpe_proxy']}, trades {row['performance']['total_trades']}"
        )
    lines.extend([
        "",
        "## Interpretation",
        "- Daily contextual baseline is the steadier broad-market lane.",
        "- Monday-buy Friday-sell lane is the higher-upside tactical lane.",
        "- Fast-veto variants test whether cutting noisy weekly entries improves return/drawdown frontier.",
        "",
        "## Outputs",
        f"- json: {json_path}",
    ])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"REPORT_JSON={json_path}")
    print(f"REPORT_MD={md_path}")
    print(f"BEST_BRANCH={best['branch_id']}")
    print(f"BEST_RETURN_PCT={best['performance']['total_return_pct']}")


if __name__ == "__main__":
    main()
