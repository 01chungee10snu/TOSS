#!/usr/bin/env python3
"""Reversal-optimized backtest: pure oversold bounce strategy.

Based on diagnostic findings:
- Reversal (bottom mom_5d) → +1.63% 1d, +1.84% 3d (before cost)
- Momentum (top mom_5d) → -0.15% 3d (negative!)
- High vol stocks have more edge

Strategy: Buy most oversold stocks, hold 3 days, wider TP/SL.
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
    df["mom_1d"] = df.groupby("code")["Close"].pct_change(1)
    df["vol_20d"] = df.groupby("code")["ret_daily"].transform(lambda s: s.rolling(20).std())
    df["intraday_range"] = (df["High"] - df["Low"]) / df["prev_close"]
    df["dollar_volume"] = df["Close"] * df["Volume"]

    delta = df.groupby("code")["Close"].diff()
    gain = delta.clip(lower=0).groupby(df["code"]).transform(lambda s: s.rolling(14).mean())
    loss = (-delta.clip(upper=0)).groupby(df["code"]).transform(lambda s: s.rolling(14).mean())
    rs = gain / (loss + 1e-8)
    df["rsi_14"] = 100 - (100 / (1 + rs))

    vol_ma5 = df.groupby("code")["Volume"].transform(lambda s: s.rolling(5).mean())
    vol_ma20 = df.groupby("code")["Volume"].transform(lambda s: s.rolling(20).mean())
    df["vol_ratio"] = vol_ma5 / (vol_ma20 + 1e-8)

    for d in range(1, 6):
        df[f"fwd_high_{d}d"] = df.groupby("code")["High"].shift(-d)
        df[f"fwd_low_{d}d"] = df.groupby("code")["Low"].shift(-d)
        df[f"fwd_close_{d}d"] = df.groupby("code")["Close"].shift(-d)

    return df


def detect_market(market_row):
    ret = market_row.get("ret_daily", 0)
    vol = market_row.get("vol_20d", 0)
    if pd.isna(ret) or pd.isna(vol):
        return "neutral"
    if ret < -0.005:
        return "down"
    elif ret > 0.005:
        return "up"
    return "flat"


def select_reversal_candidates(day_data, market="neutral"):
    """Select most oversold stocks for reversal bounce."""
    cands = day_data.copy()
    # Filter: liquid, positive price, sufficient data
    cands = cands[
        (cands["dollar_volume"] >= 5e8)
        & (cands["Close"] >= 2000)
        & cands["mom_5d"].notna()
        & cands["rsi_14"].notna()
        & cands["vol_20d"].notna()
    ]

    if cands.empty:
        return cands

    # Reversal score: lower mom_5d (more oversold) = better
    # Combine: oversold mom_5d + low RSI + high dollar volume (liquidity)
    cands = cands.copy()
    cands["rev_score"] = (
        (1 - cands["mom_5d"].rank(pct=True)) * 0.40   # most oversold
        + (1 - cands["rsi_14"] / 100).rank(pct=True) * 0.35  # low RSI
        + cands["dollar_volume"].rank(pct=True) * 0.15   # liquid
        + cands["vol_20d"].rank(pct=True) * 0.10   # higher vol = bigger bounce
    )

    # Filter: must be actually oversold (negative 5d momentum)
    cands = cands[cands["mom_5d"] < 0]

    return cands.nlargest(3, "rev_score")


def simulate_exit(entry, row, tp_pct, sl_pct, trail_pct, max_hold):
    tp = entry * (1 + tp_pct)
    sl = entry * (1 - sl_pct)
    peak = entry

    for d in range(1, max_hold + 1):
        h = row.get(f"fwd_high_{d}d")
        l = row.get(f"fwd_low_{d}d")
        c = row.get(f"fwd_close_{d}d")
        if pd.isna(h) or pd.isna(l):
            return entry, "no_data", d - 1

        peak = max(peak, h)
        trail = peak * (1 - trail_pct)

        if l <= sl:
            return sl, "stop_loss", d
        if h >= tp:
            return tp, "take_profit", d
        if peak > entry and l <= trail:
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
    print(f"Period: {tradeable[0].date()} → {tradeable[-1].date()}, {len(tradeable)} days")

    # Grid search
    tp_grid = [0.04, 0.05, 0.06, 0.08, 0.10]
    sl_grid = [0.03, 0.04, 0.05]
    trail_grid = [0.03, 0.04, 0.05]
    hold_grid = [2, 3, 4, 5]
    topn_grid = [1, 2, 3]

    combos = list(itertools.product(tp_grid, sl_grid, trail_grid, hold_grid, topn_grid))
    print(f"Total combos: {len(combos)}")

    day_groups = {d: df[df["Date"] == d] for d in tradeable}

    results = []

    for ci, (tp, sl, trail, hold, topn) in enumerate(combos):
        if ci % 30 == 0:
            print(f"  [{ci}/{len(combos)}] tp={tp} sl={sl} trail={trail} hold={hold} top={topn}")

        trades = []

        for date in tradeable:
            day_data = day_groups[date]
            if day_data.empty or date not in market_df.index:
                continue
            mkt = market_df.loc[date]
            market = detect_market(mkt)

            cands = select_reversal_candidates(day_data, market)
            if cands.empty:
                continue

            for _, row in cands.head(topn).iterrows():
                entry = row["Close"]
                exit_price, reason, days_held = simulate_exit(entry, row, tp, sl, trail, hold)
                if reason == "no_data" or exit_price <= 0:
                    continue
                gross = exit_price / entry - 1
                net = gross - COST_RATE
                pnl = net * MAX_NOTIONAL
                trades.append({
                    "date": str(date.date()), "symbol": row["code"], "market": market,
                    "net_ret": net, "pnl": pnl, "reason": reason, "days_held": days_held,
                    "mom_5d": row["mom_5d"], "rsi": row["rsi_14"],
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

        results.append({
            "tp": tp, "sl": sl, "trail": trail, "hold": hold, "topn": topn,
            "total_pnl": round(total_pnl, 0), "trades": n, "trade_days": trade_days,
            "win_rate": round(wr, 1), "profit_factor": round(min(pf, 999), 2),
            "avg_ret_pct": round(avg_ret, 2), "max_dd": round(max_dd, 0), "sharpe": round(sharpe, 2),
        })

    elapsed = time.time() - t0
    rdf = pd.DataFrame(results)

    positive = rdf[rdf["total_pnl"] > 0]
    print(f"\nSweep done in {elapsed:.0f}s. {len(rdf)} configs, {len(positive)} positive.\n")

    # Top by P&L
    top_pnl = rdf.nlargest(15, "total_pnl")
    print("=" * 100)
    print("🏆 Top 15 by Total P&L")
    print("=" * 100)
    print(top_pnl.to_string(index=False))

    # Top by Sharpe
    top_sharpe = rdf.nlargest(15, "sharpe")
    print()
    print("=" * 100)
    print("📈 Top 15 by Sharpe")
    print("=" * 100)
    print(top_sharpe.to_string(index=False))

    print()
    print(f"✅ Positive P&L configs: {len(positive)} / {len(rdf)}")
    if len(positive) > 0:
        best = positive.nlargest(1, "total_pnl").iloc[0]
        print(f"   Best: TP={best.tp} SL={best.sl} Trail={best.trail} Hold={best.hold} TopN={best.topn}")
        print(f"   P&L={best.total_pnl:+,.0f}원 Sharpe={best.sharpe} WR={best.win_rate}% PF={best.profit_factor} Trades={best.trades}")

    # Save
    out = ROOT / "reports" / "harness" / "reversal_sweep_results.json"
    out.write_text(json.dumps({
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "strategy": "pure_reversal_oversold_bounce",
        "total_configs": len(rdf),
        "positive_configs": len(positive),
        "top15_by_pnl": top_pnl.to_dict("records"),
        "top15_by_sharpe": top_sharpe.to_dict("records"),
        "all_results": rdf.to_dict("records"),
    }, indent=2, ensure_ascii=False))
    print(f"\n💾 저장: {out}")


if __name__ == "__main__":
    run()
