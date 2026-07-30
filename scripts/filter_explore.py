#!/usr/bin/env python3
"""Deep filter exploration on oversold reversal base.

Base: bottom-5% by mom_5d (most oversold), hold 5 days
Test every combination of additional filters to find the real edge.
"""
from __future__ import annotations
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
    df["mom_1d"] = df.groupby("code")["Close"].pct_change(1)
    df["mom_5d"] = df.groupby("code")["Close"].pct_change(5)
    df["mom_10d"] = df.groupby("code")["Close"].pct_change(10)
    df["mom_20d"] = df.groupby("code")["Close"].pct_change(20)
    df["vol_20d"] = df.groupby("code")["ret_daily"].transform(lambda s: s.rolling(20).std())
    df["vol_5d"] = df.groupby("code")["ret_daily"].transform(lambda s: s.rolling(5).std())
    df["dollar_volume"] = df["Close"] * df["Volume"]
    df["intraday_range"] = (df["High"] - df["Low"]) / df["prev_close"]

    delta = df.groupby("code")["Close"].diff()
    gain = delta.clip(lower=0).groupby(df["code"]).transform(lambda s: s.rolling(14).mean())
    loss = (-delta.clip(upper=0)).groupby(df["code"]).transform(lambda s: s.rolling(14).mean())
    rs = gain / (loss + 1e-8)
    df["rsi_14"] = 100 - (100 / (1 + rs))

    vol_ma5 = df.groupby("code")["Volume"].transform(lambda s: s.rolling(5).mean())
    vol_ma20 = df.groupby("code")["Volume"].transform(lambda s: s.rolling(20).mean())
    df["vol_ratio"] = vol_ma5 / (vol_ma20 + 1e-8)

    # BB width
    df["sma_20"] = df.groupby("code")["Close"].transform(lambda s: s.rolling(20).mean())
    df["bb_std"] = df.groupby("code")["Close"].transform(lambda s: s.rolling(20).std())
    df["bb_lower"] = df["sma_20"] - 2 * df["bb_std"]
    df["bb_upper"] = df["sma_20"] + 2 * df["bb_std"]
    df["below_bb_lower"] = (df["Close"] < df["bb_lower"]).astype(int)

    # Distance from 20d mean
    df["dist_sma20"] = (df["Close"] / df["sma_20"] - 1)

    for d in [1, 2, 3, 5, 7, 10]:
        df[f"fwd_{d}d"] = df.groupby("code")["Close"].shift(-d) / df["Close"] - 1

    return df


def market_regime(df):
    """Compute market regime from KOSPI proxy."""
    mkt = df[df["code"] == KOSPI_PROXY].copy()
    mkt["mkt_ret"] = mkt["Close"].pct_change()
    mkt["mkt_mom_5d"] = mkt["Close"].pct_change(5)
    mkt["mkt_vol_20d"] = mkt["ret_daily"].rolling(20).std()
    mkt = mkt[["Date", "mkt_ret", "mkt_mom_5d", "mkt_vol_20d"]].set_index("Date")
    return mkt


def sim(df_in, hold=5, topn=3, quantile=0.05):
    """Run simulation, return metrics dict."""
    all_dates = sorted(df_in["Date"].unique())
    tradeable = [d for d in all_dates if d >= all_dates[25]]
    daily_pnl = []
    n_trades = 0

    for date in tradeable:
        day = df_in[df_in["Date"] == date]
        if day.empty:
            continue
        thresh = day["mom_5d"].quantile(quantile) if "mom_5d" in day.columns else None
        if thresh is None:
            continue
        oversold = day[day["mom_5d"] <= thresh]
        if oversold.empty:
            continue
        picks = oversold.nlargest(topn, "dollar_volume")
        col = f"fwd_{hold}d"
        rets = picks[col].dropna()
        if len(rets) == 0:
            continue
        avg = rets.mean() - COST
        daily_pnl.append(avg * NOTIONAL * len(rets))
        n_trades += len(rets)

    if not daily_pnl:
        return None

    pnl = pd.Series(daily_pnl)
    cum = pnl.cumsum()
    wr_day = (pnl > 0).mean() * 100
    peak = cum.cummax()
    max_dd = (cum - peak).min()
    sharpe = pnl.mean() / (pnl.std() + 1e-8) * np.sqrt(252)
    years = len(tradeable) / 252
    annual = (pnl.sum() / (years * NOTIONAL * 3)) * 100 if years > 0 else 0

    return {
        "total_pnl": round(pnl.sum(), 0),
        "trades": n_trades,
        "trade_days": len(pnl),
        "win_rate_day": round(wr_day, 1),
        "sharpe": round(sharpe, 2),
        "max_dd": round(max_dd, 0),
        "annual_pct": round(annual, 1),
    }


def main():
    print("Loading data...")
    df = load()
    mkt = market_regime(df)
    df = df.merge(mkt, on="Date", how="left")

    valid = df.dropna(subset=["mom_5d", "dollar_volume", "fwd_5d", "rsi_14", "vol_20d", "vol_ratio"])
    valid = valid[valid["dollar_volume"] >= 5e8]

    print(f"Base universe: {len(valid)} rows, {valid['Date'].nunique()} days")
    print(f"Period: {valid['Date'].min().date()} → {valid['Date'].Date.max() if hasattr(valid['Date'], 'Date') else valid['Date'].max().date()}")
    print()

    # ── Base case ──────────────────────────────────────────────
    print("=" * 80)
    print("BASE: Bottom-5% oversold, top-3 by $vol, 5d hold, no TP/SL")
    print("=" * 80)
    base = sim(valid, hold=5, topn=3, quantile=0.05)
    print(f"  P&L={base['total_pnl']:+,.0f}  Sharpe={base['sharpe']}  WR={base['win_rate_day']}%  Trades={base['trades']}  Annual={base['annual_pct']}%")
    print()

    # ── Single filters ────────────────────────────────────────
    print("=" * 80)
    print("SINGLE FILTER ADDITIONS (on top of base)")
    print("=" * 80)

    filters = {
        "rsi<30": valid["rsi_14"] < 30,
        "rsi<25": valid["rsi_14"] < 25,
        "rsi<20": valid["rsi_14"] < 20,
        "vol_ratio>1.5": valid["vol_ratio"] > 1.5,
        "vol_ratio>2.0": valid["vol_ratio"] > 2.0,
        "below_bb_lower": valid["below_bb_lower"] == 1,
        "dist_sma20<-5%": valid["dist_sma20"] < -0.05,
        "dist_sma20<-10%": valid["dist_sma20"] < -0.10,
        "mom_1d<-3%": valid["mom_1d"] < -0.03,
        "mom_1d<-5%": valid["mom_1d"] < -0.05,
        "mom_10d<-10%": valid["mom_10d"] < -0.10,
        "vol_20d>5%": valid["vol_20d"] > 0.05,
        "mkt_down": valid["mkt_ret"] < -0.005,
        "mkt_up": valid["mkt_ret"] > 0.005,
        "mkt_5d_down>3%": valid["mkt_mom_5d"] < -0.03,
        "mkt_5d_down>5%": valid["mkt_mom_5d"] < -0.05,
        "price>10000": valid["Close"] > 10000,
        "price>50000": valid["Close"] > 50000,
        "high_vol+low_rsi": (valid["vol_20d"] > 0.04) & (valid["rsi_14"] < 30),
        "bb+vol_spike": (valid["below_bb_lower"] == 1) & (valid["vol_ratio"] > 1.3),
        "bb+rsi<30": (valid["below_bb_lower"] == 1) & (valid["rsi_14"] < 30),
        "deep_oversold+bb": (valid["mom_5d"] < -0.05) & (valid["below_bb_lower"] == 1),
        "deep_oversold+rsi<25": (valid["mom_5d"] < -0.05) & (valid["rsi_14"] < 25),
        "deep_oversold+vol_spike": (valid["mom_5d"] < -0.05) & (valid["vol_ratio"] > 1.5),
        "3_factor": (valid["mom_5d"] < -0.05) & (valid["rsi_14"] < 30) & (valid["vol_ratio"] > 1.3),
        "4_factor": (valid["mom_5d"] < -0.05) & (valid["rsi_14"] < 30) & (valid["below_bb_lower"] == 1) & (valid["vol_ratio"] > 1.2),
    }

    results = []
    print(f"{'Filter':<30} {'P&L':>12} {'Sharpe':>8} {'WR%':>6} {'N':>6} {'Annual%':>8} {'MaxDD':>12}")
    print("-" * 90)

    for name, mask in filters.items():
        sub = valid[mask].copy()
        if len(sub) < 50:
            continue
        m = sim(sub, hold=5, topn=3, quantile=0.05)
        if m is None:
            continue
        results.append({"filter": name, **m, "universe_size": len(sub)})
        print(f"  {name:<28} {m['total_pnl']:>+10,.0f}원 {m['sharpe']:>7.2f} {m['win_rate_day']:>5.1f}% {m['trades']:>6} {m['annual_pct']:>7.1f}% {m['max_dd']:>+10,.0f}원")

    # ── Market regime conditional ──────────────────────────────
    print()
    print("=" * 80)
    print("MARKET REGIME CONDITIONAL (base strategy in different regimes)")
    print("=" * 80)

    regimes = {
        "mkt_up": valid["mkt_ret"] > 0.005,
        "mkt_flat": (valid["mkt_ret"] >= -0.005) & (valid["mkt_ret"] <= 0.005),
        "mkt_down": valid["mkt_ret"] < -0.005,
        "mkt_5d_up": valid["mkt_mom_5d"] > 0.02,
        "mkt_5d_down": valid["mkt_mom_5d"] < -0.02,
        "mkt_5d_crash": valid["mkt_mom_5d"] < -0.05,
        "mkt_high_vol": valid["mkt_vol_20d"] > 0.015,
        "mkt_low_vol": valid["mkt_vol_20d"] <= 0.015,
    }

    print(f"{'Regime':<20} {'P&L':>12} {'Sharpe':>8} {'WR%':>6} {'N':>6} {'Annual%':>8}")
    print("-" * 70)
    for name, mask in regimes.items():
        sub = valid[mask].copy()
        if len(sub) < 50:
            continue
        m = sim(sub, hold=5, topn=3, quantile=0.05)
        if m is None:
            continue
        print(f"  {name:<18} {m['total_pnl']:>+10,.0f}원 {m['sharpe']:>7.2f} {m['win_rate_day']:>5.1f}% {m['trades']:>6} {m['annual_pct']:>7.1f}%")

    # ── Hold period comparison ────────────────────────────────
    print()
    print("=" * 80)
    print("HOLD PERIOD COMPARISON (base strategy)")
    print("=" * 80)
    print(f"{'Hold':>6} {'P&L':>12} {'Sharpe':>8} {'WR%':>6} {'Annual%':>8} {'MaxDD':>12}")
    print("-" * 60)
    for hold in [1, 2, 3, 5, 7, 10]:
        m = sim(valid, hold=hold, topn=3, quantile=0.05)
        if m:
            print(f"  {hold}d  {m['total_pnl']:>+10,.0f}원 {m['sharpe']:>7.2f} {m['win_rate_day']:>5.1f}% {m['annual_pct']:>7.1f}% {m['max_dd']:>+10,.0f}원")

    # ── Top-N comparison ──────────────────────────────────────
    print()
    print("=" * 80)
    print("TOP-N COMPARISON (5d hold)")
    print("=" * 80)
    print(f"{'TopN':>6} {'P&L':>12} {'Sharpe':>8} {'WR%':>6} {'Annual%':>8}")
    print("-" * 50)
    for topn in [1, 2, 3, 5]:
        m = sim(valid, hold=5, topn=topn, quantile=0.05)
        if m:
            print(f"  {topn}    {m['total_pnl']:>+10,.0f}원 {m['sharpe']:>7.2f} {m['win_rate_day']:>5.1f}% {m['annual_pct']:>7.1f}%")

    # ── Best combo found: print detailed ──────────────────────
    if results:
        best = max(results, key=lambda x: x["sharpe"])
        print()
        print("=" * 80)
        print(f"BEST FILTER: {best['filter']}")
        print(f"  P&L={best['total_pnl']:+,.0f}원  Sharpe={best['sharpe']}  WR={best['win_rate_day']}%  Trades={best['trades']}  Annual={best['annual_pct']}%  MaxDD={best['max_dd']:+,.0f}원")
        print("=" * 80)


if __name__ == "__main__":
    main()
