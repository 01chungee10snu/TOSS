#!/usr/bin/env python3
"""Improved strategy v2: oversold reversal + BB + RSI + market regime filter.

Key findings from filter exploration:
- bb+rsi<30: Sharpe 1.12, best risk-adjusted among multi-filter combos
- mkt_down conditional: Sharpe 1.10 (strategy works best in down markets)
- mkt_high_vol: Sharpe 0.76 (high vol regime is favorable)
- 5-7 day hold is optimal (3d = breakeven, 1d = loss)
- Top-2 by dollar volume is sweet spot (concentration vs diversification)

Strategy:
1. Entry: RSI<30 AND below BB lower band AND bottom-5% mom_5d
2. Regime filter: activate when market is down or high-vol
3. Pick: top-2 by dollar volume
4. Exit: hold 5 days, optional wide SL (8%) for tail risk
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
COST = 0.00245
NOTIONAL = 250_000


def load():
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

    df["sma_20"] = df.groupby("code")["Close"].transform(lambda s: s.rolling(20).mean())
    df["bb_std"] = df.groupby("code")["Close"].transform(lambda s: s.rolling(20).std())
    df["bb_lower"] = df["sma_20"] - 2 * df["bb_std"]

    vol_ma5 = df.groupby("code")["Volume"].transform(lambda s: s.rolling(5).mean())
    vol_ma20 = df.groupby("code")["Volume"].transform(lambda s: s.rolling(20).mean())
    df["vol_ratio"] = vol_ma5 / (vol_ma20 + 1e-8)

    for d in range(1, 8):
        df[f"fwd_high_{d}d"] = df.groupby("code")["High"].shift(-d)
        df[f"fwd_low_{d}d"] = df.groupby("code")["Low"].shift(-d)
        df[f"fwd_close_{d}d"] = df.groupby("code")["Close"].shift(-d)

    # Market proxy
    mkt = df[df["code"] == KOSPI_PROXY][["Date", "ret_daily", "vol_20d"]].copy()
    mkt.columns = ["Date", "mkt_ret", "mkt_vol"]
    mkt["mkt_mom_5d"] = mkt["mkt_ret"].rolling(5).sum()
    df = df.merge(mkt, on="Date", how="left")

    return df


def simulate_exit(entry, row, tp_pct, sl_pct, max_hold):
    """Exit sim: SL check first, then hold to close. No trailing (kills edge)."""
    sl_price = entry * (1 - sl_pct) if sl_pct > 0 else 0

    for d in range(1, max_hold + 1):
        h = row.get(f"fwd_high_{d}d")
        l = row.get(f"fwd_low_{d}d")
        c = row.get(f"fwd_close_{d}d")
        if pd.isna(l) or pd.isna(c):
            return entry, "no_data", d - 1

        if l <= sl_price and sl_pct > 0:
            return sl_price, "stop_loss", d

    fc = row.get(f"fwd_close_{max_hold}d")
    if pd.isna(fc):
        return entry, "no_data", max_hold
    return fc, "max_hold_close", max_hold


def run_strategy(df, rsi_max, use_bb, regime_filter, hold, topn, sl_pct, label=""):
    """Run single strategy config, return metrics + trade list."""
    valid = df.dropna(subset=["mom_5d", "dollar_volume", "rsi_14", "vol_20d", f"fwd_close_{hold}d", "mkt_ret"])
    valid = valid[valid["dollar_volume"] >= 5e8]

    # Core filter: oversold
    mask = valid["mom_5d"] <= valid["mom_5d"].quantile(0.05)

    # RSI filter
    if rsi_max < 100:
        mask &= valid["rsi_14"] < rsi_max

    # BB filter
    if use_bb:
        mask &= valid["Close"] < valid["bb_lower"]

    # Regime filter
    if regime_filter == "down_only":
        mask &= valid["mkt_ret"] < 0
    elif regime_filter == "down_or_highvol":
        mask &= (valid["mkt_ret"] < 0.002) | (valid["mkt_vol"] > 0.015)
    elif regime_filter == "crash":
        mask &= valid["mkt_mom_5d"] < -0.02

    candidates = valid[mask]
    if candidates.empty:
        return None, []

    all_dates = sorted(candidates["Date"].unique())
    tradeable = [d for d in all_dates if d >= all_dates[25]]

    trades = []
    for date in tradeable:
        day = candidates[candidates["Date"] == date]
        if day.empty:
            continue
        picks = day.nlargest(topn, "dollar_volume")
        for _, row in picks.iterrows():
            entry = row["Close"]
            exit_price, reason, days_held = simulate_exit(entry, row, 0, sl_pct, hold)
            if reason == "no_data" or exit_price <= 0:
                continue
            gross = exit_price / entry - 1
            net = gross - COST
            pnl = net * NOTIONAL
            trades.append({
                "date": str(date.date()), "symbol": row["code"],
                "net_ret": net, "pnl": pnl, "reason": reason,
                "days_held": days_held, "rsi": row["rsi_14"],
                "mom_5d": row["mom_5d"], "mkt_ret": row["mkt_ret"],
            })

    if not trades:
        return None, []

    tdf = pd.DataFrame(trades)
    total_pnl = tdf["pnl"].sum()
    n = len(tdf)
    wins = tdf[tdf["pnl"] > 0]
    losses = tdf[tdf["pnl"] <= 0]
    wr = len(wins) / n * 100
    pf = wins["pnl"].sum() / abs(losses["pnl"].sum()) if len(losses) > 0 and losses["pnl"].sum() != 0 else 999

    daily = tdf.groupby("date")["pnl"].sum()
    cum = daily.cumsum()
    peak = cum.cummax()
    dd = cum - peak
    max_dd = dd.min()
    daily_ret = daily / 1_000_000
    sharpe = daily_ret.mean() / (daily_ret.std() + 1e-8) * np.sqrt(252) if daily_ret.std() > 0 else 0
    years = len(tradeable) / 252
    annual = (total_pnl / (years * NOTIONAL * topn)) * 100 if years > 0 else 0

    return {
        "label": label,
        "total_pnl": round(total_pnl, 0),
        "trades": n,
        "trade_days": tdf["date"].nunique(),
        "win_rate": round(wr, 1),
        "profit_factor": round(min(pf, 999), 2),
        "avg_ret_pct": round(tdf["net_ret"].mean() * 100, 2),
        "max_dd": round(max_dd, 0),
        "sharpe": round(sharpe, 2),
        "annual_pct": round(annual, 1),
    }, tdf


def main():
    t0 = time.time()
    print("Loading data...")
    df = load()
    print(f"Data: {df['Date'].min().date()} → {df['Date'].max().date()}, {len(df)} rows")
    print()

    configs = [
        # (rsi_max, use_bb, regime_filter, hold, topn, sl_pct, label)
        (100, False, "none", 5, 3, 0, "BASE: oversold only, 5d, top3"),
        (30, False, "none", 5, 3, 0, "oversold+RSI<30, 5d, top3"),
        (30, True, "none", 5, 3, 0, "oversold+RSI<30+BB, 5d, top3"),
        (30, True, "down_only", 5, 3, 0, "oversold+RSI<30+BB+mkt_down, 5d, top3"),
        (30, True, "down_or_highvol", 5, 3, 0, "oversold+RSI<30+BB+down/highvol, 5d, top3"),
        (25, True, "none", 5, 3, 0, "oversold+RSI<25+BB, 5d, top3"),
        (25, True, "down_only", 5, 3, 0, "oversold+RSI<25+BB+mkt_down, 5d, top3"),
        (30, True, "none", 5, 2, 0, "oversold+RSI<30+BB, 5d, top2"),
        (30, True, "none", 7, 2, 0, "oversold+RSI<30+BB, 7d, top2"),
        (30, True, "none", 5, 2, 0.08, "oversold+RSI<30+BB, 5d, top2, SL8%"),
        (30, True, "none", 7, 2, 0.08, "oversold+RSI<30+BB, 7d, top2, SL8%"),
        (30, True, "down_or_highvol", 5, 2, 0, "oversold+RSI<30+BB+down/highvol, 5d, top2"),
        (30, True, "down_or_highvol", 7, 2, 0, "oversold+RSI<30+BB+down/highvol, 7d, top2"),
        (30, True, "down_or_highvol", 7, 2, 0.08, "oversold+RSI<30+BB+down/highvol, 7d, top2, SL8%"),
        (30, True, "crash", 5, 2, 0, "oversold+RSI<30+BB+crash, 5d, top2"),
        (30, True, "crash", 7, 2, 0.08, "oversold+RSI<30+BB+crash, 7d, top2, SL8%"),
        # Top-1 for concentration
        (30, True, "none", 5, 1, 0, "oversold+RSI<30+BB, 5d, top1"),
        (30, True, "none", 7, 1, 0.08, "oversold+RSI<30+BB, 7d, top1, SL8%"),
        (25, True, "down_or_highvol", 7, 2, 0.08, "FULL: RSI<25+BB+down/highvol, 7d, top2, SL8%"),
        # Pure market filter without RSI/BB
        (100, False, "down_only", 5, 3, 0, "oversold+mkt_down only, 5d, top3"),
        (100, False, "down_or_highvol", 7, 2, 0, "oversold+down/highvol, 7d, top2"),
    ]

    results = []
    detailed = {}

    print(f"{'Config':<55} {'P&L':>10} {'Sharpe':>7} {'WR%':>6} {'PF':>5} {'N':>5} {'Annual%':>8} {'MaxDD':>10}")
    print("-" * 115)

    for rsi_max, use_bb, regime, hold, topn, sl, label in configs:
        m, trades = run_strategy(df, rsi_max, use_bb, regime, hold, topn, sl, label)
        if m is None:
            print(f"  {label:<53}  -- no trades --")
            continue
        results.append(m)
        detailed[label] = trades
        print(f"  {label:<53} {m['total_pnl']:>+9,.0f}원 {m['sharpe']:>6.2f} {m['win_rate']:>5.1f}% {m['profit_factor']:>5.2f} {m['trades']:>5} {m['annual_pct']:>7.1f}% {m['max_dd']:>+9,.0f}원")

    # Best configs
    print()
    print("=" * 115)
    best_sharpe = max(results, key=lambda x: x["sharpe"])
    print(f"🎯 BEST SHARPE: {best_sharpe['label']}")
    print(f"   P&L={best_sharpe['total_pnl']:+,.0f}원  Sharpe={best_sharpe['sharpe']}  WR={best_sharpe['win_rate']}%  PF={best_sharpe['profit_factor']}  Trades={best_sharpe['trades']}  Annual={best_sharpe['annual_pct']}%")

    best_pnl = max(results, key=lambda x: x["total_pnl"])
    print(f"\n💰 BEST P&L: {best_pnl['label']}")
    print(f"   P&L={best_pnl['total_pnl']:+,.0f}원  Sharpe={best_pnl['sharpe']}  WR={best_pnl['win_rate']}%  PF={best_pnl['profit_factor']}  Trades={best_pnl['trades']}  Annual={best_pnl['annual_pct']}%")

    # Pick the recommended config and show monthly breakdown
    recommended_label = "oversold+RSI<30+BB+down/highvol, 7d, top2, SL8%"
    if recommended_label in detailed:
        print()
        print("=" * 115)
        print(f"📅 MONTHLY BREAKDOWN: {recommended_label}")
        print("=" * 115)
        tdf = detailed[recommended_label]
        tdf["month"] = pd.to_datetime(tdf["date"]).dt.to_period("M")
        monthly = tdf.groupby("month").agg(
            trades=("pnl", "count"),
            pnl=("pnl", "sum"),
            win_rate=("pnl", lambda x: (x > 0).mean() * 100),
        )
        monthly["cum_pnl"] = monthly["pnl"].cumsum()
        print(monthly.to_string())

    # Save
    out = ROOT / "reports" / "harness" / "strategy_v2_comparison.json"
    out.write_text(json.dumps({
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "configs_tested": len(results),
        "all_results": results,
        "best_sharpe": best_sharpe,
        "best_pnl": best_pnl,
    }, indent=2, ensure_ascii=False))
    print(f"\n💾 저장: {out}")
    print(f"⏱ {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
