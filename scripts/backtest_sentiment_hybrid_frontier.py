"""Fine-grid validation for the quant + sentiment hybrid overlay.

Research only. No live orders.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from backtest_sentiment_overlay import build_sentiment_map, filter_sentiment_map_by_year, run_engine

PANEL_CSV = ROOT / "reports/backtests/random500_seed20260607_2022-01-01_2026-latest_ohlcv_panel.csv"
SENT_CSV = ROOT / "reports/harness/news_sentiment_panel_20260621.csv"
OUT_DIR = ROOT / "reports/harness"
ALPHAS = [0.10, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70, 0.75, 0.90, 1.00, 1.25, 1.50, 2.00]
COST_BPS = [10.0, 30.0, 50.0]
YEARS = [2025, 2026]


def label_alpha(alpha: float) -> str:
    return str(alpha).replace(".", "p")


def objective(summary: dict[str, Any]) -> float:
    """Risk-adjusted ranking objective for frontier comparisons."""
    return (
        float(summary["total_return_pct"])
        + 2.0 * float(summary["sharpe_ratio"])
        + 0.5 * float(summary["max_drawdown_pct"])
    )


def main() -> None:
    panel = pd.read_csv(PANEL_CSV, dtype={"code": str}, parse_dates=["Date"])
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    sent_df = pd.read_csv(SENT_CSV, parse_dates=["date"])
    sent_map = build_sentiment_map(sent_df)

    rows: list[dict[str, Any]] = []
    for year in YEARS:
        yr_sent_map = filter_sentiment_map_by_year(sent_map, year)
        if not yr_sent_map:
            continue
        for cost in COST_BPS:
            base = run_engine(panel, cost_bps=cost, year=year)["summary"]
            row = dict(base)
            row.update({"candidate": "canonical_base", "overlay": "none", "alpha": None, "year": year, "cost_bps": cost})
            row["objective"] = objective(row)
            rows.append(row)
            rerank = run_engine(panel, sentiment_map=yr_sent_map, overlay_mode="rerank", cost_bps=cost, year=year)["summary"]
            row = dict(rerank)
            row.update({"candidate": "sentiment_rerank", "overlay": "rerank", "alpha": None, "year": year, "cost_bps": cost})
            row["objective"] = objective(row)
            rows.append(row)
            for alpha in ALPHAS:
                result = run_engine(panel, sentiment_map=yr_sent_map, overlay_mode="hybrid", alpha=alpha, cost_bps=cost, year=year)["summary"]
                row = dict(result)
                row.update({
                    "candidate": f"sentiment_hybrid_a{label_alpha(alpha)}",
                    "overlay": "hybrid",
                    "alpha": alpha,
                    "year": year,
                    "cost_bps": cost,
                })
                row["objective"] = objective(row)
                rows.append(row)
                print(
                    f"year={year} cost={cost:.0f} alpha={alpha:.2f} "
                    f"ret={row['total_return_pct']:.2f} mdd={row['max_drawdown_pct']:.2f} "
                    f"sharpe={row['sharpe_ratio']:.4f} trades={row['total_trades']}"
                )

    df = pd.DataFrame(rows)
    result_csv = OUT_DIR / "sentiment_hybrid_frontier_20260621.csv"
    report_md = OUT_DIR / "sentiment_hybrid_frontier_20260621.md"
    result_json = OUT_DIR / "sentiment_hybrid_frontier_20260621.json"
    df.to_csv(result_csv, index=False)

    # Base-relative deltas per year/cost.
    enriched = []
    for (year, cost), group in df.groupby(["year", "cost_bps"]):
        base = group[group["candidate"] == "canonical_base"].iloc[0]
        g = group.copy()
        g["return_delta_vs_base"] = g["total_return_pct"] - base["total_return_pct"]
        g["mdd_delta_vs_base"] = g["max_drawdown_pct"] - base["max_drawdown_pct"]
        g["sharpe_delta_vs_base"] = g["sharpe_ratio"] - base["sharpe_ratio"]
        enriched.append(g)
    out = pd.concat(enriched, ignore_index=True)

    leaderboard = out.sort_values(["year", "cost_bps", "objective"], ascending=[True, True, False])
    top_by_year_cost = leaderboard.groupby(["year", "cost_bps"]).head(5)
    alpha30 = out[(out["overlay"] == "hybrid") & (out["cost_bps"] == 30.0)].copy()
    alpha_stability = alpha30.groupby("alpha").agg(
        mean_return=("total_return_pct", "mean"),
        min_return=("total_return_pct", "min"),
        mean_sharpe=("sharpe_ratio", "mean"),
        max_mdd=("max_drawdown_pct", "min"),
        total_trades=("total_trades", "sum"),
    ).reset_index().sort_values(["mean_return", "mean_sharpe"], ascending=False)

    payload = {
        "result_csv": str(result_csv),
        "top_by_year_cost": top_by_year_cost.to_dict(orient="records"),
        "alpha_stability_30bps": alpha_stability.to_dict(orient="records"),
    }
    result_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    lines = [
        "# Sentiment hybrid frontier — 2026-06-21",
        "",
        "Paper/research only. live_order_submitted: False.",
        "",
        "## Algorithm",
        "`hybrid_score = quant_rank_pct + alpha * sentiment_rank_pct`.",
        "`final_score` remains the original quantitative score, so the base quality threshold remains active.",
        "",
        "## Files",
        f"- csv: `{result_csv}`",
        f"- json: `{result_json}`",
        "",
        "## Top by year/cost/objective",
        top_by_year_cost[["year", "cost_bps", "candidate", "total_return_pct", "max_drawdown_pct", "total_trades", "win_rate_pct", "sharpe_ratio", "objective", "return_delta_vs_base"]].to_markdown(index=False),
        "",
        "## 30bps alpha stability across 2025/2026",
        alpha_stability.to_markdown(index=False),
    ]
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"saved {result_csv}")
    print(alpha_stability.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
