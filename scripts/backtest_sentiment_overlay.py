"""Backtest sentiment overlay using collected news sentiment data.

Builds a forward-filled sentiment_map from news articles and injects it
into the canonical ReplayEngine as a penalty overlay.

Only 2025-06 to 2026-06 is testable (Google News data coverage).
We compare:
  1. canonical_base (no sentiment)
  2. sentiment_penalty (base_score + alpha * sentiment)
  3. sentiment_rerank (base filter, sentiment re-orders)

Paper/research only. live_order_submitted: False.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from toss_alpha.daily import decision as _decision_mod
from toss_alpha.daily import replay as _replay_mod
from toss_alpha.daily.replay import ReplayEngine

PANEL_CSV = ROOT / "reports/backtests/random500_seed20260607_2022-01-01_2026-latest_ohlcv_panel.csv"
SENT_CSV = ROOT / "reports/harness/news_sentiment_panel_20260621.csv"
OUT_DIR = ROOT / "reports/harness"
ENGINE_BASE = {
    "step": 5,
    "score_threshold": 55,
    "stop_loss_pct": 0.12,
    "take_profit_pct": 0.20,
    "max_holding_steps": 10,
    "max_positions": 4,
    "trailing_stop_pct": 0.0,
    "sizing_mode": "flat",
    "rebalance_mode": "hold_until_exit",
    "min_volume": 0,
}


def symbols_of(panel: pd.DataFrame) -> list[str]:
    return sorted(panel["code"].astype(str).str.zfill(6).unique().tolist())


def build_sentiment_map(sent_df: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Build date→{code→score} map from news sentiment data.

    Forward-fills: for each date, uses the most recent sentiment score
    available for each symbol (within a 30-day lookback window).
    """
    sent_df = sent_df.copy()
    sent_df["code"] = sent_df["code"].astype(str).str.zfill(6)
    sent_df["date"] = pd.to_datetime(sent_df["date"])

    # Daily average per symbol
    daily = sent_df.groupby(["code", sent_df["date"].dt.date])["sentiment_score"].mean().reset_index()
    daily.columns = ["code", "date", "score"]
    daily["date"] = pd.to_datetime(daily["date"])
    daily = daily.sort_values(["code", "date"])

    # Build forward-filled map for all dates in range
    all_dates = sorted(daily["date"].dt.date.unique())
    date_str_min = pd.Timestamp(all_dates[0]).date().isoformat()
    date_str_max = pd.Timestamp(all_dates[-1]).date().isoformat()

    result: dict[str, dict[str, float]] = {}

    # For each code, create a forward-filled series
    codes = daily["code"].unique()
    code_date_scores: dict[str, dict[str, float]] = {}
    for code in codes:
        code_data = daily[daily["code"] == code].set_index("date")["score"]
        code_date_scores[code] = {d.date().isoformat(): float(v) for d, v in code_data.items()}

    # For each date, assign most recent score per code (within 30-day lookback)
    for d in all_dates:
        date_str = pd.Timestamp(d).date().isoformat()
        result[date_str] = {}
        cutoff = pd.Timestamp(d) - pd.Timedelta(days=30)
        for code, scores in code_date_scores.items():
            # Find most recent score on or before this date
            recent = {k: v for k, v in scores.items() if pd.Timestamp(k) <= pd.Timestamp(d) and pd.Timestamp(k) >= cutoff}
            if recent:
                latest_date = max(recent.keys())
                result[date_str][code] = recent[latest_date]

    return result


def filter_sentiment_map_by_year(sent_map: dict, year: int) -> dict:
    return {d: s for d, s in sent_map.items() if pd.Timestamp(d).year == year}


def run_engine(panel: pd.DataFrame, *, sentiment_map=None, overlay_mode=None, alpha=10.0, cost_bps=30.0, year=None) -> dict[str, Any]:
    p = panel if year is None else panel[panel["Date"].dt.year == year].copy()
    cfg = dict(ENGINE_BASE)
    step = int(cfg.pop("step"))

    kwargs = dict(
        panel=p,
        symbols=symbols_of(p),
        initial_cash_krw=1_000_000,
        max_notional_krw=100_000,
        transaction_cost_bps=cost_bps,
        prediction_map=sentiment_map,
    )
    if overlay_mode:
        kwargs["prediction_overlay_mode"] = overlay_mode
        kwargs["prediction_alpha"] = alpha

    engine = ReplayEngine(**kwargs, **cfg)
    return engine.run(step=step)


def main() -> None:
    print("Loading panel...")
    panel = pd.read_csv(PANEL_CSV, dtype={"code": str}, parse_dates=["Date"])
    panel["code"] = panel["code"].astype(str).str.zfill(6)

    print("Loading sentiment data...")
    sent_df = pd.read_csv(SENT_CSV, parse_dates=["date"])
    print(f"  {len(sent_df)} rows, {sent_df['code'].nunique()} symbols")
    print(f"  Date range: {sent_df['date'].min()} ~ {sent_df['date'].max()}")

    print("Building sentiment_map...")
    sent_map = build_sentiment_map(sent_df)
    print(f"  {len(sent_map)} dates in map")

    # Test on 2026 only (best sentiment coverage)
    test_years = [2025, 2026]
    rows = []

    for year in test_years:
        print(f"\n=== YEAR {year} ===")
        yr_sent_map = filter_sentiment_map_by_year(sent_map, year)
        yr_panel = panel[panel["Date"].dt.year == year]

        # Only test dates that have sentiment data
        sent_dates = set(yr_sent_map.keys())
        panel_dates = set(yr_panel["Date"].dt.date.astype(str).unique())
        overlap = sent_dates & panel_dates
        print(f"  Sentiment dates: {len(sent_dates)}, Panel dates: {len(panel_dates)}, Overlap: {len(overlap)}")

        if not overlap:
            print(f"  No overlap, skipping {year}")
            continue

        # 1. Base (no sentiment)
        print(f"  Running base...")
        base_result = run_engine(panel, cost_bps=30.0, year=year)
        base_summary = dict(base_result["summary"])
        base_summary.update({"candidate": "canonical_base", "year": year, "overlay": "none"})
        rows.append(base_summary)
        print(f"    return={base_summary['total_return_pct']:.2f}% trades={base_summary['total_trades']}")

        # 2. Sentiment penalty with different alphas
        for alpha in [5.0, 10.0, 20.0, 50.0]:
            print(f"  Running sentiment_penalty alpha={alpha}...")
            result = run_engine(panel, sentiment_map=yr_sent_map, overlay_mode="penalty", alpha=alpha, cost_bps=30.0, year=year)
            summary = dict(result["summary"])
            summary.update({"candidate": f"sentiment_penalty_a{alpha:.0f}", "year": year, "overlay": "penalty"})
            rows.append(summary)
            print(f"    return={summary['total_return_pct']:.2f}% trades={summary['total_trades']}")

        # 3. Hybrid rank blend: quant rank + alpha * sentiment rank.
        # final_score remains the original quant score for base-quality gating.
        for alpha in [0.25, 0.5, 1.0, 2.0]:
            label = str(alpha).replace(".", "p")
            print(f"  Running sentiment_hybrid alpha={alpha}...")
            result = run_engine(panel, sentiment_map=yr_sent_map, overlay_mode="hybrid", alpha=alpha, cost_bps=30.0, year=year)
            summary = dict(result["summary"])
            summary.update({"candidate": f"sentiment_hybrid_a{label}", "year": year, "overlay": "hybrid"})
            rows.append(summary)
            print(f"    return={summary['total_return_pct']:.2f}% trades={summary['total_trades']}")

        # 4. Sentiment rerank
        print(f"  Running sentiment_rerank...")
        result = run_engine(panel, sentiment_map=yr_sent_map, overlay_mode="rerank", cost_bps=30.0, year=year)
        summary = dict(result["summary"])
        summary.update({"candidate": "sentiment_rerank", "year": year, "overlay": "rerank"})
        rows.append(summary)
        print(f"    return={summary['total_return_pct']:.2f}% trades={summary['total_trades']}")

    df = pd.DataFrame(rows)
    result_csv = OUT_DIR / "sentiment_overlay_backtest_20260621.csv"
    report_md = OUT_DIR / "sentiment_overlay_backtest_20260621.md"
    df.to_csv(result_csv, index=False)

    lines = [
        "# Sentiment overlay backtest — 2026-06-21",
        "",
        "Paper/research only. live_order_submitted: False.",
        "",
        "## Method",
        "- News sentiment from KLUE-RoBERTa on Google News RSS titles (490 symbols).",
        "- Forward-filled 30-day lookback sentiment_map.",
        "- Overlay modes: penalty (base + alpha * sentiment), rerank.",
        "- Tested on 2025 and 2026 (sentiment data coverage).",
        "",
        "## Files",
        f"- results: `{result_csv}`",
        "",
        "## Results",
        df.to_markdown(index=False),
    ]
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\nSaved to {result_csv}")
    print(f"\n=== RESULTS ===")
    print(df[["candidate", "year", "total_return_pct", "max_drawdown_pct", "total_trades", "win_rate_pct", "sharpe_ratio"]].to_string(index=False))


if __name__ == "__main__":
    main()
