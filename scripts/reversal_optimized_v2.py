#!/usr/bin/env python3
"""Optimized reversal strategy: bottom-5% oversold, 5-day hold, wide TP/SL.

Based on diagnostic findings:
- Bottom 5% by mom_5d → +1.14% at 5d after cost (strongest edge)
- 1-day hold destroys edge via cost drag
- TP/SL too tight cuts winners
"""
from __future__ import annotations

import json
import itertools
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PANEL_CSV = ROOT / "reports" / "backtests" / "practical_universe_400_2022-01-01_2026-latest_ohlcv_panel.csv"
KOSPI_PROXY = "005930"
ROUND_TRIP_BPS = 24.5
COST_RATE = ROUND_TRIP_BPS / 10_000
MAX_NOTIONAL = 250_000


def load_and_compute():
    df = pd.read_csv(PANEL_CSV, dtype={"code": str}, parse_dates=["Date"])
    df = df.sort_values(["code", "Date"]).reset_index(drop=True)

    df["prev_close"] = df.groupby("code")["Close"].shift(1)
    df["ret_daily"] = df.groupby("code")["Close"].pct_change()
    df["mom_5d"] = df.groupby("code")["Close"].pct_change(5)
    df["vol_20d"] = df.groupby("code")["ret_daily"].transform(lambda s: s.rolling(20).std())
    df["dollar_volume"] = df["Close"] * df["Volume"]

    delta = df.groupby("code")["Close"].diff()
    gain = delta.clip(lower=0).groupby(df["code"]).transform(lambda s: s.rolling(14).mean())
    loss = (-delta.clip(upper=0)).groupby(df["code"]).transform(lambda s: s.rolling(14).mean())
    rs = gain / (loss + 1e-8)
    df["rsi_14"] = 100 - (100 / (1 + rs))

    vol_ma5 = df.groupby("code")["Volume"].transform(lambda s: s.rolling(5).mean())
    vol_ma20 = df.groupby("code")["Volume"].transform(lambda s: s.rolling(20).mean())
    df["vol_ratio"] = vol_ma5 / (vol_ma20 + 1e-8)

    for d in range(1, 7):
        df[f"fwd_high_{d}d"] = df.groupby("code")["High"].shift(-d)
        df[f"fwd_low_{d}d"] = df.groupby("code")["Low"].shift(-d)
        df[f"fwd_close_{d}d"] = df.groupby("code")["Close"].shift(-d)

    return df


def simulate_exit(entry, row, tp_pct, sl_pct, trail_pct, max_hold):
    tp = entry * (1 + tp_pct) if tp_pct > 0 else float("inf")
    sl = entry * (1 - sl_pct) if sl_pct > 0 else 0
    peak = entry

    for d in range(1, max_hold + 1):
        h = row.get(f"fwd_high_{d}d")
        l = row.get(f"fwd_low_{d}d")
        c = row.get(f"fwd_close_{d}d")
        if pd.isna(h) or pd.isna(l):
            return entry, "no_data", d - 1

        peak = max(peak, h)
        trail = peak * (1 - trail_pct) if trail_pct > 0 else 0

        if l <= sl:
            return sl, "stop_loss", d
        if h >= tp:
            return tp, "take_profit", d
        if trail_pct > 0 and peak > entry and l <= trail:
            return trail, "trailing_stop", d

    fc = row.get(f"fwd_close_{max_hold}d")
    if pd.isna(fc):
        return entry, "no_data", max_hold
    return fc, "max_hold_close", max_hold


def run():
    t0 = time.time()
    print("Loading data...")
    df = load_and_compute()

    market_df = df[df["code"] == KOSPI_PROXY].set_index("Date")
    all_dates = sorted(df["Date"].unique())
    tradeable = [d for d in all_dates if d >= all_dates[25]]

    day_groups = {d: df[df["Date"] == d] for d in tradeable}

    # Grid: focused on wide/no TP/SL + 5-day hold
    # tp=0 means no take profit (hold to max_hold)
    tp_grid = [0.0, 0.06, 0.08, 0.10, 0.15]
    sl_grid = [0.0, 0.05, 0.06, 0.08]
    trail_grid = [0.0, 0.05, 0.08]
    hold_grid = [3, 5, 7]
    pct_grid = [0.05, 0.10]  # bottom 5% or 10% by mom_5d
    topn_grid = [1, 2, 3]

    combos = list(itertools.product(tp_grid, sl_grid, trail_grid, hold_grid, pct_grid, topn_grid))
    print(f"Period: {tradeable[0].date()} → {tradeable[-1].date()}, {len(tradeable)} days")
    print(f"Total combos: {len(combos)}")

    results = []

    for ci, (tp, sl, trail, hold, pct, topn) in enumerate(combos):
        if ci % 40 == 0:
            print(f"  [{ci}/{len(combos)}] tp={tp} sl={sl} trail={trail} hold={hold} pct={pct} top={topn}")

        trades = []

        for date in tradeable:
            day_data = day_groups[date]
            if day_data.empty:
                continue

            cands = day_data.copy()
            cands = cands[
                (cands["dollar_volume"] >= 5e8)
                & (cands["Close"] >= 2000)
                & cands["mom_5d"].notna()
                & cands["vol_20d"].notna()
            ]
            if cands.empty:
                continue

            # Select bottom pct% by mom_5d (most oversold)
            threshold = cands["mom_5d"].quantile(pct)
            oversold = cands[cands["mom_5d"] <= threshold]
            if oversold.empty:
                continue

            # Pick top-N by dollar volume
            picks = oversold.nlargest(topn, "dollar_volume")

            for _, row in picks.iterrows():
                entry = row["Close"]
                exit_price, reason, days_held = simulate_exit(entry, row, tp, sl, trail, hold)
                if reason == "no_data" or exit_price <= 0:
                    continue
                gross = exit_price / entry - 1
                net = gross - COST_RATE
                pnl = net * MAX_NOTIONAL
                trades.append({
                    "date": str(date.date()), "symbol": row["code"],
                    "net_ret": net, "pnl": pnl, "reason": reason, "days_held": days_held,
                    "mom_5d": row["mom_5d"],
                })

        if not trades:
            continue

        tdf = pd.DataFrame(trades)
        total_pnl = tdf["pnl"].sum()
        n = len(tdf)
        wins = tdf[tdf["pnl"] > 0]
        losses = tdf[tdf["pnl"] <= 0]
        wr = len(wins) / n * 100
        pf = wins["pnl"].sum() / abs(losses["pnl"].sum()) if len(losses) > 0 and losses["pnl"].sum() != 0 else 999
        avg_ret = tdf["net_ret"].mean() * 100

        daily = tdf.groupby("date")["pnl"].sum()
        cum = daily.cumsum()
        peak = cum.cummax()
        dd = cum - peak
        max_dd = dd.min()
        daily_ret = daily / 1_000_000
        sharpe = daily_ret.mean() / (daily_ret.std() + 1e-8) * np.sqrt(252) if daily_ret.std() > 0 else 0
        trade_days = tdf["date"].nunique()

        # Annualized return (approx)
        years = len(tradeable) / 252
        annual_ret = (total_pnl / (years * 750_000)) * 100 if years > 0 else 0  # ~750K avg deployed

        results.append({
            "tp": tp, "sl": sl, "trail": trail, "hold": hold, "pct": pct, "topn": topn,
            "total_pnl": round(total_pnl, 0), "trades": n, "trade_days": trade_days,
            "win_rate": round(wr, 1), "profit_factor": round(min(pf, 999), 2),
            "avg_ret_pct": round(avg_ret, 2), "max_dd": round(max_dd, 0),
            "sharpe": round(sharpe, 2), "annual_ret_pct": round(annual_ret, 1),
        })

    elapsed = time.time() - t0
    rdf = pd.DataFrame(results)

    positive = rdf[rdf["total_pnl"] > 0]
    print(f"\nSweep done in {elapsed:.0f}s. {len(rdf)} configs, {len(positive)} positive.\n")

    top_pnl = rdf.nlargest(20, "total_pnl")
    print("=" * 110)
    print("🏆 Top 20 by Total P&L")
    print("=" * 110)
    print(top_pnl.to_string(index=False))

    top_sharpe = rdf.nlargest(15, "sharpe")
    print()
    print("=" * 110)
    print("📈 Top 15 by Sharpe")
    print("=" * 110)
    print(top_sharpe.to_string(index=False))

    print()
    print(f"✅ Positive P&L configs: {len(positive)} / {len(rdf)}")
    if len(positive) > 0:
        best = positive.nlargest(1, "sharpe").iloc[0]
        print(f"\n   🎯 Best risk-adjusted: TP={best.tp} SL={best.sl} Trail={best.trail} Hold={best.hold}d Bottom={best.pct} TopN={best.topn}")
        print(f"   P&L={best.total_pnl:+,.0f}원 Sharpe={best.sharpe} WR={best.win_rate}% PF={best.profit_factor} Trades={best.trades}")

        best_pnl = positive.nlargest(1, "total_pnl").iloc[0]
        print(f"\n   💰 Best absolute P&L: TP={best_pnl.tp} SL={best_pnl.sl} Trail={best_pnl.trail} Hold={best_pnl.hold}d Bottom={best_pnl.pct} TopN={best_pnl.topn}")
        print(f"   P&L={best_pnl.total_pnl:+,.0f}원 Sharpe={best_pnl.sharpe} WR={best_pnl.win_rate}% PF={best_pnl.profit_factor}")

    out = ROOT / "reports" / "harness" / "reversal_optimized_sweep.json"
    out.write_text(json.dumps({
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "strategy": "oversold_reversal_5d_hold",
        "total_configs": len(rdf),
        "positive_configs": len(positive),
        "top20_by_pnl": top_pnl.to_dict("records"),
        "top15_by_sharpe": top_sharpe.to_dict("records"),
        "all_results": rdf.to_dict("records"),
    }, indent=2, ensure_ascii=False))
    print(f"\n💾 저장: {out}")


if __name__ == "__main__":
    run()
