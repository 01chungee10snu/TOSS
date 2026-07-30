#!/usr/bin/env python3
"""Diagnostic: pure buy & hold returns (no TP/SL) by momentum decile."""
from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PANEL_CSV = ROOT / "reports" / "backtests" / "practical_universe_400_2022-01-01_2026-latest_ohlcv_panel.csv"
COST = 0.00245


def main():
    df = pd.read_csv(PANEL_CSV, dtype={"code": str}, parse_dates=["Date"])
    df = df.sort_values(["code", "Date"]).reset_index(drop=True)

    df["prev_close"] = df.groupby("code")["Close"].shift(1)
    df["ret_daily"] = df.groupby("code")["Close"].pct_change()
    df["mom_5d"] = df.groupby("code")["Close"].pct_change(5)
    df["vol_20d"] = df.groupby("code")["ret_daily"].transform(lambda s: s.rolling(20).std())
    df["dollar_volume"] = df["Close"] * df["Volume"]

    for d in [1, 2, 3, 5]:
        df[f"fwd_{d}d"] = df.groupby("code")["Close"].shift(-d) / df["Close"] - 1

    valid = df.dropna(subset=["mom_5d", "fwd_3d", "vol_20d", "dollar_volume"])
    valid = valid[valid["dollar_volume"] >= 5e8]

    print("=" * 70)
    print("NO TP/SL: Pure buy & hold forward returns (after 24.5bps cost)")
    print("=" * 70)

    valid["mom_decile"] = pd.qcut(valid["mom_5d"], 10, labels=False, duplicates="drop")
    print("\nBy mom_5d decile (0=most oversold, 9=most overbought):")
    print(f"{'Decile':<8} {'1d':>8} {'2d':>8} {'3d':>8} {'5d':>8} {'N':>8}")
    for d in range(10):
        sub = valid[valid["mom_decile"] == d]
        r1 = (sub["fwd_1d"].mean() - COST) * 100
        r2 = (sub["fwd_2d"].mean() - COST) * 100
        r3 = (sub["fwd_3d"].mean() - COST) * 100
        r5 = (sub["fwd_5d"].mean() - COST) * 100
        print(f"  D{d}     {r1:>+7.2f}% {r2:>+7.2f}% {r3:>+7.2f}% {r5:>+7.2f}% {len(sub):>8}")

    print("\n" + "=" * 70)
    print("SIMULATION: Buy most oversold (bottom decile), hold N days, NO TP/SL")
    print("=" * 70)

    all_dates = sorted(valid["Date"].unique())
    tradeable = [d for d in all_dates if d >= all_dates[25]]

    for hold_days in [1, 2, 3, 5]:
        daily_pnl = []
        for date in tradeable:
            day_data = valid[valid["Date"] == date]
            if day_data.empty:
                continue
            threshold = day_data["mom_5d"].quantile(0.10)
            oversold = day_data[day_data["mom_5d"] <= threshold]
            if oversold.empty:
                continue
            picks = oversold.nlargest(3, "dollar_volume")
            col = f"fwd_{hold_days}d"
            rets = picks[col].dropna()
            if len(rets) == 0:
                continue
            avg_ret = rets.mean() - COST
            daily_pnl.append(avg_ret * 250000 * len(rets))

        if not daily_pnl:
            print(f"  Hold {hold_days}d: no trades")
            continue

        pnl = pd.Series(daily_pnl)
        cum = pnl.cumsum()
        total = pnl.sum()
        win = (pnl > 0).sum()
        wr = win / len(pnl) * 100
        peak = cum.cummax()
        max_dd = (cum - peak).min()
        sharpe = pnl.mean() / (pnl.std() + 1e-8) * np.sqrt(252)
        print(f"  Hold {hold_days}d: P&L={total:>+10,.0f}원  Days={len(pnl)}  WinDay%={wr:.1f}%  MaxDD={max_dd:>+10,.0f}원  Sharpe={sharpe:.2f}")

    print("\n" + "=" * 70)
    print("BENCHMARK: Buy ALL liquid stocks, equal weight, hold N days")
    print("=" * 70)
    for hold_days in [1, 3, 5]:
        col = f"fwd_{hold_days}d"
        all_rets = valid[col].dropna() - COST
        print(f"  Hold {hold_days}d: avg_ret={all_rets.mean()*100:>+.3f}%  win_rate={(all_rets>0).mean()*100:.1f}%  N={len(all_rets)}")

    # Now check: oversold + high volume ratio (volume spike = capitulation)
    print("\n" + "=" * 70)
    print("FILTER COMBOS: oversold + volume spike + low RSI")
    print("=" * 70)

    # RSI
    delta = df.groupby("code")["Close"].diff()
    gain = delta.clip(lower=0).groupby(df["code"]).transform(lambda s: s.rolling(14).mean())
    loss = (-delta.clip(upper=0)).groupby(df["code"]).transform(lambda s: s.rolling(14).mean())
    rs = gain / (loss + 1e-8)
    df["rsi_14"] = 100 - (100 / (1 + rs))
    vol_ma5 = df.groupby("code")["Volume"].transform(lambda s: s.rolling(5).mean())
    vol_ma20 = df.groupby("code")["Volume"].transform(lambda s: s.rolling(20).mean())
    df["vol_ratio"] = vol_ma5 / (vol_ma20 + 1e-8)

    valid2 = df.dropna(subset=["mom_5d", "fwd_3d", "vol_20d", "dollar_volume", "rsi_14", "vol_ratio"])
    valid2 = valid2[valid2["dollar_volume"] >= 5e8]

    # Various filters
    filters = {
        "oversold_5pct": valid2["mom_5d"] <= valid2["mom_5d"].quantile(0.05),
        "oversold_10pct": valid2["mom_5d"] <= valid2["mom_5d"].quantile(0.10),
        "oversold+rsi<30": (valid2["mom_5d"] < -0.03) & (valid2["rsi_14"] < 30),
        "oversold+vol_spike": (valid2["mom_5d"] < -0.03) & (valid2["vol_ratio"] > 1.5),
        "oversold+rsi<35+vol_spike": (valid2["mom_5d"] < -0.02) & (valid2["rsi_14"] < 35) & (valid2["vol_ratio"] > 1.2),
        "deep_oversold": valid2["mom_5d"] < -0.05,
        "deep_oversold+vol_spike": (valid2["mom_5d"] < -0.05) & (valid2["vol_ratio"] > 1.3),
        "rsi<25": valid2["rsi_14"] < 25,
        "rsi<30": valid2["rsi_14"] < 30,
    }

    print(f"\n{'Filter':<35} {'N':>6} {'1d':>8} {'3d':>8} {'5d':>8}")
    print("-" * 70)
    for name, mask in filters.items():
        sub = valid2[mask]
        if len(sub) < 10:
            continue
        r1 = (sub["fwd_1d"].mean() - COST) * 100
        r3 = (sub["fwd_3d"].mean() - COST) * 100
        r5 = (sub["fwd_5d"].mean() - COST) * 100
        print(f"  {name:<33} {len(sub):>6} {r1:>+7.2f}% {r3:>+7.2f}% {r5:>+7.2f}%")


if __name__ == "__main__":
    main()
