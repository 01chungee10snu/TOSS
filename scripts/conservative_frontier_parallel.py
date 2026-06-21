"""Parallel conservative frontier: max_positions × risk_exit × sizing.

Research/paper only. No live orders. The sizing_frontier covered 0.30-0.45%/day
but the conservative 0.10-0.20%/day band has only 7 combos, all with fixed
risk/exit params. This loop sweeps max_positions and risk_exit tightness across
both strategies to fill the conservative band and find better risk-adjusted
returns at every daily-target level.
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

OUT_CSV = ROOT / "reports/harness/conservative_frontier_20260621.csv"
OUT_MD = ROOT / "reports/harness/conservative_frontier_20260621.md"

YEAR_PANELS: dict[int, pd.DataFrame] = {}
YEAR_SYMBOLS: dict[int, list[str]] = {}
PRED_MAPS: dict[tuple[int, str], dict[str, dict[str, float]] | None] = {}


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
        cash_fraction_per_entry=float(task["cash_fraction_per_entry"]),
        **cfg,
    )
    r = engine.run(step=step)
    s = dict(r["summary"])
    s.update(task)
    s["daily_avg_pct_252"] = daily_avg_pct(float(s["total_return_pct"]))
    return s


def main() -> None:
    print("=== Parallel Conservative Frontier ===", flush=True)
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
        raise FileNotFoundError(f"Macro cache not found at {macro_path}")
    sent_df = pd.read_csv(SENT_CSV, parse_dates=["date"])
    sent_map = build_sentiment_map(sent_df)

    for year in [2024, 2025, 2026]:
        YEAR_PANELS[year] = panel[panel["Date"].dt.year == year].copy()
        YEAR_SYMBOLS[year] = symbols_of(YEAR_PANELS[year])
        PRED_MAPS[(year, "base")] = None

    print("Training ML/fusion maps...", flush=True)
    for train_end, year in [(2023, 2024), (2024, 2025), (2025, 2026)]:
        model = train_ml_model(features_df, train_end)
        ml_map = predict_ml_scores(model, features_df, year)
        yr_sent = filter_sentiment_by_year(sent_map, year)
        fused_map = compute_macro_adjusted_scores(ml_map, macro_df, yr_sent if yr_sent else None)
        PRED_MAPS[(year, "ml_rerank")] = ml_map
        PRED_MAPS[(year, "fusion_rerank")] = fused_map
        print(f"  ready train≤{train_end} trade {year}", flush=True)

    # Risk/exit tightness presets
    RISK_PRESETS = {
        "ultra_tight": {"stop_loss_pct": 0.03, "take_profit_pct": 0.10, "trailing_stop_pct": 0.03, "max_holding_steps": 5},
        "tight":       {"stop_loss_pct": 0.05, "take_profit_pct": 0.15, "trailing_stop_pct": 0.04, "max_holding_steps": 7},
        "medium":      {"stop_loss_pct": 0.08, "take_profit_pct": 0.20, "trailing_stop_pct": 0.05, "max_holding_steps": 15},
        "loose":       {"stop_loss_pct": 0.10, "take_profit_pct": 0.25, "trailing_stop_pct": 0.06, "max_holding_steps": 20},
    }

    STRATEGIES = [
        {"strategy": "fusion_rerank", "overlay_mode": "rerank", "alpha": 10.0},
        {"strategy": "ml_rerank", "overlay_mode": "rerank", "alpha": 10.0},
    ]

    MAX_POSITIONS = [2, 3, 4, 6, 8]
    SIZING = [
        {"cash_fraction_per_entry": 0.20, "max_notional": 100_000},
        {"cash_fraction_per_entry": 0.30, "max_notional": 200_000},
        {"cash_fraction_per_entry": 0.40, "max_notional": 300_000},
        {"cash_fraction_per_entry": 0.50, "max_notional": 300_000},
    ]

    tasks: list[dict[str, Any]] = []
    for train_end, year in [(2023, 2024), (2024, 2025), (2025, 2026)]:
        for strat in STRATEGIES:
            for rp_name, rp in RISK_PRESETS.items():
                for mp_ in MAX_POSITIONS:
                    for sz in SIZING:
                        t = dict(strat)
                        t.update(rp)
                        t.update(sz)
                        t.update({
                            "max_positions": mp_,
                            "risk_preset": rp_name,
                            "train_end": train_end,
                            "trade_year": year,
                        })
                        tasks.append(t)

    jobs = int(os.environ.get("TOSS_FRONTIER_JOBS", "14"))
    print(f"Running {len(tasks)} tasks with fork pool jobs={jobs}...", flush=True)
    ctx = mp.get_context("fork")
    with ctx.Pool(processes=jobs) as pool:
        rows = list(pool.imap_unordered(run_one, tasks, chunksize=3))

    df = pd.DataFrame(rows).sort_values([
        "trade_year", "strategy", "risk_preset", "max_positions",
        "cash_fraction_per_entry", "max_notional",
    ])
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False)

    # Aggregate: group by strategy × risk_preset × max_positions × sizing
    group_cols = ["strategy", "risk_preset", "max_positions", "cash_fraction_per_entry", "max_notional"]
    agg = df.groupby(group_cols).agg(
        mean_return=("total_return_pct", "mean"),
        min_return=("total_return_pct", "min"),
        mean_daily=("daily_avg_pct_252", "mean"),
        max_mdd=("max_drawdown_pct", "min"),
        mean_sharpe=("sharpe_ratio", "mean"),
        years=("trade_year", "count"),
    ).reset_index()
    agg["all_positive"] = agg["min_return"] > 0
    agg = agg.sort_values(["all_positive", "mean_sharpe"], ascending=[False, False])

    # Build report
    lines = [
        "# Conservative Frontier: max_positions × risk_exit × sizing",
        "",
        "Research/paper only. No live orders submitted.",
        "",
        "## Interpretation",
        "",
        "- Sweeps risk/exit tightness (ultra_tight→loose), max_positions (2→8), and conservative sizing.",
        "- Goal: fill 0.10-0.30%/day band and find best risk-adjusted returns at each level.",
        "",
        "## Robust leaderboard (3yr all-positive, sorted by Sharpe)",
        "",
    ]
    robust = agg[agg["all_positive"]]
    for _, row in robust.head(30).iterrows():
        lines.append(
            f"- {row['strategy']} {row['risk_preset']} maxpos={int(row['max_positions'])} "
            f"cf={row['cash_fraction_per_entry']:.2f} not={int(row['max_notional']):,}: "
            f"mean_daily={row['mean_daily']:.3f}% mean_ret={row['mean_return']:.1f}% "
            f"min_ret={row['min_return']:.1f}% mdd={row['max_mdd']:.1f}% sharpe={row['mean_sharpe']:.2f}"
        )
    lines.extend(["", "## Files", "", f"- CSV: `{OUT_CSV}`"])
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n=== Top 20 robust combos ===", flush=True)
    print(robust.head(20).to_string(index=False, float_format=lambda x: f"{x:.3f}"), flush=True)
    print(f"\nRobust combos: {len(robust)} / {len(agg)}")
    print(f"Saved: {OUT_CSV}\nReport: {OUT_MD}", flush=True)


if __name__ == "__main__":
    main()
