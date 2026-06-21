"""Parallel aggressive 0.5%/day frontier for 3-layer fusion.

Research/paper only. No live orders. Uses fork COW so the practical universe
panel, feature matrix, and prediction maps are shared across workers on macOS.
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

from toss_alpha.daily.features import compute_features
from toss_alpha.daily.replay import ReplayEngine
from backtest_sentiment_overlay import ENGINE_BASE, symbols_of
from fusion_3layer_backtest import (
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

OUT_CSV = ROOT / "reports/harness/aggressive_05pct_daily_frontier_20260621.csv"
OUT_MD = ROOT / "reports/harness/aggressive_05pct_daily_frontier_20260621.md"

YEAR_PANELS: dict[int, pd.DataFrame] = {}
YEAR_SYMBOLS: dict[int, list[str]] = {}
PRED_MAPS: dict[tuple[int, str], dict[str, dict[str, float]] | None] = {}


def daily_avg_pct(total_return_pct: float) -> float:
    return total_return_pct / 252.0


def run_one(task: dict[str, Any]) -> dict[str, Any]:
    year = int(task["trade_year"])
    strategy = str(task["strategy"])
    max_notional = float(task["max_notional"])
    max_positions = int(task["max_positions"])
    overlay_mode = task["overlay_mode"]
    alpha = float(task["alpha"])

    p = YEAR_PANELS[year]
    cfg = dict(ENGINE_BASE)
    step = int(cfg.pop("step"))
    cfg["max_positions"] = max_positions
    engine = ReplayEngine(
        panel=p,
        symbols=YEAR_SYMBOLS[year],
        initial_cash_krw=1_000_000,
        max_notional_krw=max_notional,
        transaction_cost_bps=30.0,
        prediction_map=PRED_MAPS.get((year, strategy)),
        prediction_overlay_mode=overlay_mode,
        prediction_alpha=alpha,
        **cfg,
    )
    result = engine.run(step=step)
    s = dict(result["summary"])
    s.update(
        {
            "strategy": strategy,
            "train_end": int(task["train_end"]),
            "trade_year": year,
            "max_notional": int(max_notional),
            "max_positions": max_positions,
            "daily_avg_pct_252": daily_avg_pct(float(s["total_return_pct"])),
            "target_05pct_day": daily_avg_pct(float(s["total_return_pct"])) >= 0.5,
        }
    )
    return s


def make_report(df: pd.DataFrame, agg: pd.DataFrame) -> str:
    target = 0.5
    lines = [
        "# Aggressive 0.5%/day Frontier",
        "",
        "Research/paper only. No live orders submitted.",
        "",
        "## Target",
        "",
        f"- Daily average target: {target:.2f}% over 252 trading days",
        "- Annual equivalent: about +251% compounded",
        "- Metric used here: simple total_return_pct / 252, deliberately strict and comparable across years",
        "",
        "## Robust leaderboard",
        "",
    ]
    show_cols = [
        "strategy",
        "max_notional",
        "max_positions",
        "mean_return",
        "min_return",
        "mean_daily",
        "max_mdd",
        "mean_sharpe",
        "target_years",
        "all_positive",
    ]
    for _, row in agg.head(20).iterrows():
        lines.append(
            "- "
            f"{row['strategy']} notional={int(row['max_notional']):,} maxpos={int(row['max_positions'])}: "
            f"mean_ret={row['mean_return']:.2f}%, min_ret={row['min_return']:.2f}%, "
            f"mean_daily={row['mean_daily']:.3f}%, mdd={row['max_mdd']:.2f}%, "
            f"sharpe={row['mean_sharpe']:.2f}, target_years={int(row['target_years'])}/3, "
            f"all_positive={bool(row['all_positive'])}"
        )
    lines.extend(["", "## Year-level best rows", ""])
    year_best = df.sort_values(["trade_year", "daily_avg_pct_252"], ascending=[True, False]).groupby("trade_year").head(5)
    for _, row in year_best.iterrows():
        lines.append(
            "- "
            f"{int(row['trade_year'])} {row['strategy']} notional={int(row['max_notional']):,} maxpos={int(row['max_positions'])}: "
            f"ret={row['total_return_pct']:.2f}%, daily={row['daily_avg_pct_252']:.3f}%, "
            f"mdd={row['max_drawdown_pct']:.2f}%, sharpe={row['sharpe_ratio']:.2f}, "
            f"target={bool(row['target_05pct_day'])}"
        )
    lines.extend(["", "## Files", "", f"- CSV: `{OUT_CSV}`"])
    return "\n".join(lines) + "\n"


def main() -> None:
    print("=== Parallel Aggressive 0.5%/day Fusion Frontier ===", flush=True)
    panel = pd.read_parquet(PANEL_PATH)
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    panel["Date"] = pd.to_datetime(panel["Date"])
    print(f"Panel: {len(panel):,} rows, {panel['code'].nunique()} codes", flush=True)

    print("Computing features...", flush=True)
    features_df = compute_features(panel)

    print("Loading macro/sentiment...", flush=True)
    macro_df = pd.read_parquet(MACRO_CACHE) if MACRO_CACHE.exists() else None
    if macro_df is None:
        from toss_alpha.daily.macro_signals import fetch_macro_signals

        macro_df = fetch_macro_signals()
    sent_df = pd.read_csv(SENT_CSV, parse_dates=["date"])
    sent_map = build_sentiment_map(sent_df)

    for year in [2024, 2025, 2026]:
        YEAR_PANELS[year] = panel[panel["Date"].dt.year == year].copy()
        YEAR_SYMBOLS[year] = symbols_of(YEAR_PANELS[year])
        PRED_MAPS[(year, "base")] = None

    print("Training walk-forward ML and fusion maps...", flush=True)
    for train_end, year in [(2023, 2024), (2024, 2025), (2025, 2026)]:
        model = train_ml_model(features_df, train_end)
        ml_map = predict_ml_scores(model, features_df, year)
        yr_sent = filter_sentiment_by_year(sent_map, year)
        fused_map = compute_macro_adjusted_scores(ml_map, macro_df, yr_sent if yr_sent else None)
        PRED_MAPS[(year, "ml_rerank")] = ml_map
        PRED_MAPS[(year, "fusion_rerank")] = fused_map
        PRED_MAPS[(year, "fusion_hybrid_a0p5")] = fused_map
        print(f"  ready train≤{train_end} trade {year}: ml_dates={len(ml_map)} fused_dates={len(fused_map)}", flush=True)

    tasks: list[dict[str, Any]] = []
    for train_end, year in [(2023, 2024), (2024, 2025), (2025, 2026)]:
        for max_notional in [100_000, 150_000, 200_000, 300_000, 500_000]:
            for max_positions in [4, 6, 8]:
                for strategy, mode, alpha in [
                    ("base", None, 10.0),
                    ("fusion_rerank", "rerank", 10.0),
                    ("fusion_hybrid_a0p5", "hybrid", 0.5),
                    ("ml_rerank", "rerank", 10.0),
                ]:
                    tasks.append(
                        {
                            "train_end": train_end,
                            "trade_year": year,
                            "max_notional": max_notional,
                            "max_positions": max_positions,
                            "strategy": strategy,
                            "overlay_mode": mode,
                            "alpha": alpha,
                        }
                    )
    n_jobs = int(os.environ.get("TOSS_FRONTIER_JOBS", "14"))
    print(f"Running {len(tasks)} replay tasks with fork pool jobs={n_jobs}...", flush=True)
    ctx = mp.get_context("fork")
    with ctx.Pool(processes=n_jobs) as pool:
        rows = list(pool.imap_unordered(run_one, tasks, chunksize=2))

    df = pd.DataFrame(rows).sort_values(["trade_year", "strategy", "max_notional", "max_positions"])
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False)

    agg = df.groupby(["strategy", "max_notional", "max_positions"]).agg(
        mean_return=("total_return_pct", "mean"),
        min_return=("total_return_pct", "min"),
        mean_daily=("daily_avg_pct_252", "mean"),
        max_mdd=("max_drawdown_pct", "min"),
        mean_sharpe=("sharpe_ratio", "mean"),
        target_years=("target_05pct_day", "sum"),
        years=("trade_year", "count"),
    ).reset_index()
    agg["all_positive"] = agg["min_return"] > 0
    agg = agg.sort_values(["target_years", "all_positive", "mean_daily", "mean_sharpe"], ascending=[False, False, False, False])

    OUT_MD.write_text(make_report(df, agg), encoding="utf-8")
    print("\n=== Robust leaderboard ===", flush=True)
    print(agg.head(20).to_string(index=False, float_format=lambda x: f"{x:.2f}"), flush=True)
    print(f"\nSaved: {OUT_CSV}\nReport: {OUT_MD}", flush=True)


if __name__ == "__main__":
    main()
