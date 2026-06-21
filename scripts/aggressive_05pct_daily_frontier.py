"""Aggressive 0.5%/day frontier for the 3-layer fusion strategy.

Tests whether increasing exposure / position capacity can approach the user's
stretch target of +0.5% average per trading day, while reporting drawdown.

Important: research/paper only. No live orders.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

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
OUT = ROOT / "reports/harness/aggressive_05pct_daily_frontier_20260621.csv"


def run_custom(panel: pd.DataFrame, *, year: int, prediction_map: dict | None, overlay_mode: str | None,
               alpha: float, max_notional: float, max_positions: int, cost_bps: float = 30.0) -> dict:
    p = panel[panel["Date"].dt.year == year].copy()
    cfg = dict(ENGINE_BASE)
    step = int(cfg.pop("step"))
    cfg["max_positions"] = max_positions
    engine = ReplayEngine(
        panel=p,
        symbols=symbols_of(p),
        initial_cash_krw=1_000_000,
        max_notional_krw=max_notional,
        transaction_cost_bps=cost_bps,
        prediction_map=prediction_map,
        prediction_overlay_mode=overlay_mode,
        prediction_alpha=alpha,
        **cfg,
    )
    return engine.run(step=step)


def daily_avg_pct(total_return_pct: float) -> float:
    # Replay step=5, roughly 50 replay decisions/year, but user's target is daily.
    # Use 252 trading days for stricter apples-to-apples daily target.
    return total_return_pct / 252.0


def main() -> None:
    print("=== Aggressive 0.5%/day Fusion Frontier ===")
    panel = pd.read_parquet(PANEL_PATH)
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    panel["Date"] = pd.to_datetime(panel["Date"])
    print(f"Panel: {len(panel):,} rows, {panel['code'].nunique()} codes")

    print("Computing features...")
    features_df = compute_features(panel)

    print("Loading macro/sentiment...")
    macro_df = pd.read_parquet(MACRO_CACHE) if MACRO_CACHE.exists() else None
    if macro_df is None:
        from toss_alpha.daily.macro_signals import fetch_macro_signals
        macro_df = fetch_macro_signals()
    sent_df = pd.read_csv(SENT_CSV, parse_dates=["date"])
    sent_map = build_sentiment_map(sent_df)

    grids = []
    for max_notional in [100_000, 150_000, 200_000, 300_000, 500_000]:
        for max_positions in [4, 6, 8]:
            # Skip impossible cash-heavy combos only if initial max gross too high? We allow engine cash limit.
            grids.append((max_notional, max_positions))

    rows = []
    for train_end, year in [(2023, 2024), (2024, 2025), (2025, 2026)]:
        print(f"\n--- train≤{train_end} trade {year} ---")
        model = train_ml_model(features_df, train_end)
        ml_map = predict_ml_scores(model, features_df, year)
        yr_sent = filter_sentiment_by_year(sent_map, year)
        fused_map = compute_macro_adjusted_scores(ml_map, macro_df, yr_sent if yr_sent else None)

        for max_notional, max_positions in grids:
            for label, mode, pred_map, alpha in [
                ("base", None, None, 10.0),
                ("fusion_rerank", "rerank", fused_map, 10.0),
                ("fusion_hybrid_a0p5", "hybrid", fused_map, 0.5),
                ("ml_rerank", "rerank", ml_map, 10.0),
            ]:
                r = run_custom(
                    panel,
                    year=year,
                    prediction_map=pred_map,
                    overlay_mode=mode,
                    alpha=alpha,
                    max_notional=max_notional,
                    max_positions=max_positions,
                )
                s = dict(r["summary"])
                s.update({
                    "strategy": label,
                    "train_end": train_end,
                    "trade_year": year,
                    "max_notional": max_notional,
                    "max_positions": max_positions,
                    "daily_avg_pct_252": daily_avg_pct(float(s["total_return_pct"])),
                    "target_05pct_day": daily_avg_pct(float(s["total_return_pct"])) >= 0.5,
                })
                rows.append(s)
            print(f"  done notional={max_notional:,} maxpos={max_positions}")

    df = pd.DataFrame(rows)
    df.to_csv(OUT, index=False)

    # Robust leaderboard: require all 3 years positive, then rank by mean daily avg and worst MDD.
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

    print("\n=== Robust leaderboard ===")
    print(agg.head(20).to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    print(f"\nSaved: {OUT}")


if __name__ == "__main__":
    main()
