"""Backtest the canonical ReplayEngine on the NEW practical universe.

Reuses the run_engine and build_sentiment_map from backtest_sentiment_overlay.py
for consistency. Compares NEW (practical) vs OLD (random500) universe.

Research/paper only. No live orders.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest_sentiment_overlay import (
    build_sentiment_map,
    filter_sentiment_map_by_year,
    run_engine,
)

NEW_PANEL = ROOT / "reports/backtests/practical_universe_panel.parquet"
OLD_PANEL = ROOT / "reports/backtests/random500_seed20260607_2022-01-01_2026-latest_ohlcv_panel.csv"
SENT_CSV = ROOT / "reports/harness/news_sentiment_panel_20260621.csv"
OUT_DIR = ROOT / "reports/harness"


def main() -> None:
    print("=== Backtest: New Practical Universe vs Old random500 ===")

    print(f"Loading new panel: {NEW_PANEL}")
    new_panel = pd.read_parquet(NEW_PANEL)
    new_panel["code"] = new_panel["code"].astype(str).str.zfill(6)
    new_panel["Date"] = pd.to_datetime(new_panel["Date"])
    print(f"  {len(new_panel):,} rows, {new_panel['code'].nunique()} codes")

    print(f"\nLoading old panel: {OLD_PANEL}")
    old_panel = pd.read_csv(OLD_PANEL, dtype={"code": str}, parse_dates=["Date"])
    old_panel["code"] = old_panel["code"].astype(str).str.zfill(6)
    print(f"  {len(old_panel):,} rows, {old_panel['code'].nunique()} codes")

    for label, code in [("삼성전자", "005930"), ("SK하이닉스", "000660"), ("NAVER", "035420")]:
        in_new = code in set(new_panel["code"].unique())
        in_old = code in set(old_panel["code"].unique())
        print(f"  {label}({code}): new={'✓' if in_new else '✗'} old={'✓' if in_old else '✗'}")

    print("\nLoading sentiment data...")
    sent_df = pd.read_csv(SENT_CSV, parse_dates=["date"])
    sent_map = build_sentiment_map(sent_df)
    print(f"  {len(sent_map)} dates in sentiment map")

    rows = []
    for panel_label, panel in [("NEW_practical", new_panel), ("OLD_random500", old_panel)]:
        for year in [2022, 2023, 2024, 2025, 2026]:
            yr_sent = filter_sentiment_map_by_year(sent_map, year)
            print(f"\n  [{panel_label} {year}] sentiment_dates={len(yr_sent)}")

            print(f"    base...", end=" ", flush=True)
            r = run_engine(panel, year=year)
            s = dict(r["summary"])
            s.update({"panel": panel_label, "year": year, "candidate": "base"})
            rows.append(s)
            print(f"ret={s['total_return_pct']:.2f}% mdd={s['max_drawdown_pct']:.2f}% trades={s['total_trades']}")

            if yr_sent:
                for cand, mode, alpha in [
                    ("sentiment_rerank", "rerank", None),
                    ("sentiment_hybrid_a0p5", "hybrid", 0.5),
                ]:
                    print(f"    {cand}...", end=" ", flush=True)
                    r = run_engine(panel, year=year, sentiment_map=yr_sent,
                                   overlay_mode=mode, alpha=alpha or 10.0)
                    s = dict(r["summary"])
                    s.update({"panel": panel_label, "year": year, "candidate": cand})
                    rows.append(s)
                    print(f"ret={s['total_return_pct']:.2f}% mdd={s['max_drawdown_pct']:.2f}% trades={s['total_trades']}")

    df = pd.DataFrame(rows)
    out_csv = OUT_DIR / "practical_universe_vs_random500_backtest_20260621.csv"
    df.to_csv(out_csv, index=False)
    print(f"\nSaved: {out_csv}")

    print("\n=== RETURN SUMMARY (%) ===")
    pivot = df.pivot_table(index=["panel", "candidate"], columns="year",
                           values="total_return_pct", aggfunc="first")
    print(pivot.to_string(float_format="%.2f"))

    print("\n=== MAX DRAWDOWN (%) ===")
    pivot_mdd = df.pivot_table(index=["panel", "candidate"], columns="year",
                               values="max_drawdown_pct", aggfunc="first")
    print(pivot_mdd.to_string(float_format="%.2f"))

    print("\n=== SHARPE ===")
    pivot_sharpe = df.pivot_table(index=["panel", "candidate"], columns="year",
                                  values="sharpe_ratio", aggfunc="first")
    print(pivot_sharpe.to_string(float_format="%.2f"))


if __name__ == "__main__":
    main()
