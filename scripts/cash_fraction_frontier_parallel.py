"""Cash-fraction sizing frontier for surviving 0.5%/day candidates.

Research/paper only. No live orders.

This loop starts from the two robust survivors of risk_exit_frontier_20260621:
- fusion_rerank sl=6%, tp=25%, trailing=6%, hold=20
- ml_rerank sl=10%, tp=25%, trailing=6%, hold=10

It varies max_notional and ReplayEngine.cash_fraction_per_entry to check whether
capital-utilization, not exit rules, is the remaining bottleneck to 0.5%/day.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import sys
import warnings
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_alpha.daily.features import compute_features  # noqa: E402
from toss_alpha.daily.replay import ReplayEngine  # noqa: E402
from toss_alpha.daily.macro_signals import fetch_macro_signals  # noqa: E402
from backtest_sentiment_overlay import ENGINE_BASE, symbols_of  # noqa: E402
from fusion_3layer_backtest import (  # noqa: E402
    PANEL_PATH,
    SENT_CSV,
    MACRO_CACHE,
    build_sentiment_map,
    filter_sentiment_by_year,
    train_ml_model,
    predict_ml_scores,
    compute_macro_adjusted_scores,
)

warnings.filterwarnings("ignore", category=FutureWarning)

OUT_CSV = ROOT / "reports/harness/cash_fraction_frontier_20260621.csv"
OUT_MD = ROOT / "reports/harness/cash_fraction_frontier_20260621.md"

YEAR_PANELS: dict[int, pd.DataFrame] = {}
YEAR_SYMBOLS: dict[int, list[str]] = {}
PRED_MAPS: dict[tuple[int, str], dict[str, dict[str, float]] | None] = {}

SURVIVORS = [
    {
        "strategy": "fusion_rerank",
        "overlay_mode": "rerank",
        "alpha": 10.0,
        "stop_loss_pct": 0.06,
        "take_profit_pct": 0.25,
        "trailing_stop_pct": 0.06,
        "max_holding_steps": 20,
        "max_positions": 8,
    },
    {
        "strategy": "ml_rerank",
        "overlay_mode": "rerank",
        "alpha": 10.0,
        "stop_loss_pct": 0.10,
        "take_profit_pct": 0.25,
        "trailing_stop_pct": 0.06,
        "max_holding_steps": 10,
        "max_positions": 8,
    },
]


def daily_avg_pct(total_return_pct: float) -> float:
    return total_return_pct / 252.0


def run_one(task: dict[str, Any]) -> dict[str, Any]:
    year = int(task["trade_year"])
    strategy = str(task["strategy"])
    p = YEAR_PANELS[year]
    cfg = dict(ENGINE_BASE)
    step = int(cfg.pop("step"))
    cfg.update(
        {
            "max_positions": int(task["max_positions"]),
            "stop_loss_pct": float(task["stop_loss_pct"]),
            "take_profit_pct": float(task["take_profit_pct"]),
            "trailing_stop_pct": float(task["trailing_stop_pct"]),
            "max_holding_steps": int(task["max_holding_steps"]),
            "cash_fraction_per_entry": float(task["cash_fraction_per_entry"]),
        }
    )
    engine = ReplayEngine(
        panel=p,
        symbols=YEAR_SYMBOLS[year],
        initial_cash_krw=1_000_000,
        max_notional_krw=float(task["max_notional"]),
        transaction_cost_bps=30.0,
        prediction_map=PRED_MAPS.get((year, strategy)),
        prediction_overlay_mode=task["overlay_mode"],
        prediction_alpha=float(task["alpha"]),
        **cfg,
    )
    r = engine.run(step=step)
    s = dict(r["summary"])
    s.update(task)
    s["daily_avg_pct_252"] = daily_avg_pct(float(s["total_return_pct"]))
    s["target_05pct_day"] = s["daily_avg_pct_252"] >= 0.5
    return s


def make_tasks() -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for train_end, year in [(2023, 2024), (2024, 2025), (2025, 2026)]:
        for survivor in SURVIVORS:
            for max_notional in [150_000, 200_000, 300_000, 500_000]:
                for cash_fraction in [0.25, 0.35, 0.50, 0.75, 1.00]:
                    tasks.append(
                        {
                            "train_end": train_end,
                            "trade_year": year,
                            "max_notional": max_notional,
                            "cash_fraction_per_entry": cash_fraction,
                            **survivor,
                        }
                    )
    return tasks


def make_report(df: pd.DataFrame, agg: pd.DataFrame) -> str:
    lines = [
        "# Cash-Fraction Frontier for 0.5%/day Target",
        "",
        "Research/paper only. No live orders submitted.",
        "",
        "## Interpretation",
        "",
        "- This loop varies only capital utilization after the risk/exit frontier found robust survivors.",
        "- Promotable research configs must be positive in all 2024/2025/2026 years and MDD must stay within -20%.",
        "- `cash_fraction_per_entry=0.25` is the legacy engine default.",
        "",
        "## Robust leaderboard",
        "",
    ]
    for _, row in agg.head(25).iterrows():
        lines.append(
            "- "
            f"{row['strategy']} notional={int(row['max_notional']):,} cash_frac={row['cash_fraction_per_entry']:.2f}: "
            f"mean_ret={row['mean_return']:.2f}%, min_ret={row['min_return']:.2f}%, "
            f"mean_daily={row['mean_daily']:.3f}%, max_mdd={row['max_mdd']:.2f}%, "
            f"sharpe={row['mean_sharpe']:.2f}, target_years={int(row['target_years'])}/3, "
            f"all_positive={bool(row['all_positive'])}, risk_ok={bool(row['risk_ok_20mdd'])}"
        )
    lines.extend(["", "## Year-level 0.5%/day target rows", ""])
    hits = df[df["target_05pct_day"]].sort_values(["trade_year", "total_return_pct"], ascending=[True, False])
    if hits.empty:
        lines.append("- None")
    else:
        for _, row in hits.head(60).iterrows():
            lines.append(
                "- "
                f"{int(row['trade_year'])} {row['strategy']} ret={row['total_return_pct']:.2f}% "
                f"daily={row['daily_avg_pct_252']:.3f}% mdd={row['max_drawdown_pct']:.2f}% "
                f"sharpe={row['sharpe_ratio']:.2f} notional={int(row['max_notional']):,} "
                f"cash_frac={row['cash_fraction_per_entry']:.2f}"
            )
    lines.extend(["", "## Files", "", f"- CSV: `{OUT_CSV}`"])
    return "\n".join(lines) + "\n"


def main() -> None:
    print("=== Cash-Fraction Frontier ===", flush=True)
    panel = pd.read_parquet(PANEL_PATH)
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    panel["Date"] = pd.to_datetime(panel["Date"])
    print(f"Panel: {len(panel):,} rows, {panel['code'].nunique()} codes", flush=True)

    print("Computing features...", flush=True)
    features_df = compute_features(panel)
    macro_path = MACRO_CACHE
    if not macro_path.exists():
        alt = ROOT / "src/toss_alpha/reports/harness/macro_signals.parquet"
        macro_path = alt if alt.exists() else macro_path
    if macro_path.exists():
        macro_df = pd.read_parquet(macro_path)
        print(f"Macro cache: {macro_path}", flush=True)
    else:
        print("Macro cache missing — fetching live...", flush=True)
        macro_df = fetch_macro_signals()
    sent_df = pd.read_csv(SENT_CSV, parse_dates=["date"])
    sent_map = build_sentiment_map(sent_df)

    for year in [2024, 2025, 2026]:
        YEAR_PANELS[year] = panel[panel["Date"].dt.year == year].copy()
        YEAR_SYMBOLS[year] = symbols_of(YEAR_PANELS[year])

    print("Training ML/fusion maps...", flush=True)
    for train_end, year in [(2023, 2024), (2024, 2025), (2025, 2026)]:
        model = train_ml_model(features_df, train_end)
        ml_map = predict_ml_scores(model, features_df, year)
        yr_sent = filter_sentiment_by_year(sent_map, year)
        fused_map = compute_macro_adjusted_scores(ml_map, macro_df, yr_sent if yr_sent else None)
        PRED_MAPS[(year, "ml_rerank")] = ml_map
        PRED_MAPS[(year, "fusion_rerank")] = fused_map
        print(f"  ready train≤{train_end} trade {year}", flush=True)

    tasks = make_tasks()
    jobs = int(os.environ.get("TOSS_FRONTIER_JOBS", "14"))
    print(f"Running {len(tasks)} tasks with fork pool jobs={jobs}...", flush=True)
    ctx = mp.get_context("fork")
    with ctx.Pool(processes=jobs) as pool:
        rows = list(pool.imap_unordered(run_one, tasks, chunksize=3))

    df = pd.DataFrame(rows).sort_values([
        "trade_year",
        "strategy",
        "max_notional",
        "cash_fraction_per_entry",
    ])
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False)

    group_cols = ["strategy", "max_notional", "cash_fraction_per_entry"]
    agg = df.groupby(group_cols).agg(
        mean_return=("total_return_pct", "mean"),
        min_return=("total_return_pct", "min"),
        mean_daily=("daily_avg_pct_252", "mean"),
        max_mdd=("max_drawdown_pct", "min"),
        mean_sharpe=("sharpe_ratio", "mean"),
        target_years=("target_05pct_day", "sum"),
        years=("trade_year", "count"),
    ).reset_index()
    agg["all_positive"] = agg["min_return"] > 0
    agg["risk_ok_20mdd"] = agg["max_mdd"] >= -20.0
    agg = agg.sort_values(
        ["target_years", "all_positive", "risk_ok_20mdd", "mean_daily", "mean_sharpe"],
        ascending=[False, False, False, False, False],
    )
    OUT_MD.write_text(make_report(df, agg), encoding="utf-8")

    print("\n=== Robust leaderboard ===", flush=True)
    print(agg.head(25).to_string(index=False, float_format=lambda x: f"{x:.3f}"), flush=True)
    print(f"\nTarget rows: {int(df['target_05pct_day'].sum())}", flush=True)
    print(f"Saved: {OUT_CSV}\nReport: {OUT_MD}", flush=True)


if __name__ == "__main__":
    main()
