#!/usr/bin/env python3
"""Parameter sweep: find optimal TP/SL/hold/top-N for current strategy.

Tests combinations:
- TP: [3%, 4%, 5%, 6%, 8%]
- SL: [2%, 3%, 4%, 5%]
- Hold: [1, 2, 3 days]
- Top-N: [1, 2, 3]
- Trailing: [2%, 3%, 4%]

Reports top-10 configurations by total P&L and Sharpe.
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
POLICY_JSON = ROOT / "config" / "generated_policies" / "daily_multifactor_v1_practical400.json"
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
    df["intraday_range"] = (df["High"] - df["Low"]) / df["prev_close"]
    df["gap"] = abs(df["Open"] / df["prev_close"] - 1.0)
    df["dollar_volume"] = df["Close"] * df["Volume"]

    delta = df.groupby("code")["Close"].diff()
    gain = delta.clip(lower=0).groupby(df["code"]).transform(lambda s: s.rolling(14).mean())
    loss = (-delta.clip(upper=0)).groupby(df["code"]).transform(lambda s: s.rolling(14).mean())
    rs = gain / (loss + 1e-8)
    df["rsi_14"] = 100 - (100 / (1 + rs))

    vol_ma5 = df.groupby("code")["Volume"].transform(lambda s: s.rolling(5).mean())
    vol_ma20 = df.groupby("code")["Volume"].transform(lambda s: s.rolling(20).mean())
    df["vol_ratio"] = vol_ma5 / (vol_ma20 + 1e-8)

    # Forward bars for multi-day exit sim (up to 3 days)
    for d in range(1, 5):
        df[f"fwd_high_{d}d"] = df.groupby("code")["High"].shift(-d)
        df[f"fwd_low_{d}d"] = df.groupby("code")["Low"].shift(-d)
        df[f"fwd_close_{d}d"] = df.groupby("code")["Close"].shift(-d)

    return df


def detect_situation(market_row):
    ret = market_row.get("ret_daily", 0)
    vol = market_row.get("vol_20d", 0)
    if pd.isna(ret) or pd.isna(vol):
        return "unknown", ""
    if ret > 0.005:
        d = "up"
    elif ret < -0.005:
        d = "down"
    else:
        d = "flat"
    v = "low_vol" if vol < 0.012 else "high_vol"
    situation = f"{d}_{v}"
    regime = "risk_on" if d in ("up", "flat") and v == "low_vol" else ""
    if d == "down" and v == "high_vol":
        regime = "risk_off"
    return situation, regime


def score_candidates(day_data, situation, policy):
    sit = policy.get("situations", {}).get(situation)
    if not sit or situation == "down_low_vol":
        return pd.DataFrame()
    mode = sit.get("mode", "momentum")
    w = sit.get("weights", {})
    cands = day_data.copy()
    cands = cands[
        (cands["dollar_volume"] >= sit.get("min_dollar_volume", 5e8))
        & (cands["Close"] >= sit.get("min_price", 2000))
        & (cands["mom_5d"] >= sit.get("min_mom_5d", -1))
        & (cands["mom_5d"] <= sit.get("max_mom_5d", 1))
        & (cands["rsi_14"] >= sit.get("min_rsi", 0))
        & (cands["rsi_14"] <= sit.get("max_rsi", 100))
        & (cands["vol_20d"] <= sit.get("max_vol_20d", 1))
    ]
    if cands.empty:
        return cands
    if mode == "reversal":
        ms = 1 - cands["mom_5d"].rank(pct=True)
    else:
        ms = cands["mom_5d"].rank(pct=True)
    lvs = (1 / (cands["vol_20d"] + 1e-8)).rank(pct=True)
    vns = (1 / (cands["vol_ratio"].abs() + 1e-8)).rank(pct=True)
    rs = (1 - (cands["rsi_14"] - 50).abs() / 50).rank(pct=True)
    cands = cands.copy()
    cands["score"] = w.get("momentum", 0.4) * ms + w.get("low_vol", 0.2) * lvs + w.get("vol_norm", 0.15) * vns + w.get("rsi_mid", 0.25) * rs
    return cands


def apply_veto(cands, market_regime, range_t=0.25, vol_t=0.15, relax=1.5):
    if cands.empty:
        return cands
    if market_regime == "risk_on":
        range_t *= relax
        vol_t *= relax
    return cands[(cands["intraday_range"] <= range_t) & (cands["vol_20d"] <= vol_t)].copy()


def simulate_exit_multi(entry, row, tp_pct, sl_pct, trail_pct, max_hold):
    """Multi-day exit simulation with TP/SL/trailing."""
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

        # SL check first (pessimistic)
        if l <= sl:
            return sl, "stop_loss", d
        # TP
        if h >= tp:
            return tp, "take_profit", d
        # Trailing (only after gain)
        if peak > entry and l <= trail:
            return trail, "trailing_stop", d

    # Max hold → exit at close
    fc = row.get(f"fwd_close_{max_hold}d")
    if pd.isna(fc):
        return entry, "no_data", max_hold
    return fc, "max_hold_close", max_hold


def run_sweep():
    t0 = time.time()
    print("Loading data...")
    df = load_and_compute()
    policy = json.loads(POLICY_JSON.read_text())

    market_df = df[df["code"] == KOSPI_PROXY].set_index("Date")
    all_dates = sorted(df["Date"].unique())
    tradeable = [d for d in all_dates if d >= all_dates[25]]
    print(f"Period: {tradeable[0].date()} → {tradeable[-1].date()}, {len(tradeable)} days")

    # Pre-compute per-day data
    day_groups = {d: df[df["Date"] == d] for d in tradeable}

    # Grid
    tp_grid = [0.03, 0.04, 0.05, 0.06, 0.08]
    sl_grid = [0.02, 0.03, 0.04, 0.05]
    trail_grid = [0.02, 0.03, 0.04]
    hold_grid = [1, 2, 3]
    topn_grid = [1, 2, 3]

    combos = list(itertools.product(tp_grid, sl_grid, trail_grid, hold_grid, topn_grid))
    print(f"Total combinations: {len(combos)}")

    results = []

    for ci, (tp, sl, trail, hold, topn) in enumerate(combos):
        if ci % 30 == 0:
            print(f"  [{ci}/{len(combos)}] tp={tp} sl={sl} trail={trail} hold={hold} top={topn}...")

        all_trades = []

        for date in tradeable:
            day_data = day_groups[date]
            if day_data.empty:
                continue
            if date not in market_df.index:
                continue
            mkt = market_df.loc[date]
            situation, regime = detect_situation(mkt)
            if situation == "down_low_vol":
                continue

            cands = score_candidates(day_data, situation, policy)
            if cands.empty:
                continue
            cands = apply_veto(cands, regime)
            if cands.empty:
                continue

            for _, row in cands.head(topn).iterrows():
                entry = row["Close"]
                exit_price, reason, days_held = simulate_exit_multi(entry, row, tp, sl, trail, hold)
                if reason == "no_data" or exit_price <= 0:
                    continue
                gross = exit_price / entry - 1
                net = gross - COST_RATE
                pnl = net * MAX_NOTIONAL
                all_trades.append({"date": str(date.date()), "symbol": row["code"], "net_ret": net, "pnl": pnl, "reason": reason, "days_held": days_held})

        if not all_trades:
            continue

        tdf = pd.DataFrame(all_trades)
        total_pnl = tdf["pnl"].sum()
        n = len(tdf)
        wins = tdf[tdf["pnl"] > 0]
        losses = tdf[tdf["pnl"] <= 0]
        wr = len(wins) / n * 100 if n else 0
        pf = wins["pnl"].sum() / abs(losses["pnl"].sum()) if len(losses) > 0 and losses["pnl"].sum() != 0 else 999
        avg_ret = tdf["net_ret"].mean() * 100

        # Daily P&L
        daily = tdf.groupby("date")["pnl"].sum()
        cum = daily.cumsum()
        peak = cum.cummax()
        dd = cum - peak
        max_dd = dd.min()
        daily_ret = daily / 1_000_000
        sharpe = daily_ret.mean() / (daily_ret.std() + 1e-8) * np.sqrt(252) if daily_ret.std() > 0 else 0

        # Unique trading days
        trade_days = tdf["date"].nunique()

        results.append({
            "tp": tp, "sl": sl, "trail": trail, "hold": hold, "topn": topn,
            "total_pnl": round(total_pnl, 0),
            "trades": n,
            "trade_days": trade_days,
            "win_rate": round(wr, 1),
            "profit_factor": round(pf, 2),
            "avg_ret_pct": round(avg_ret, 2),
            "max_dd": round(max_dd, 0),
            "sharpe": round(sharpe, 2),
        })

    elapsed = time.time() - t0
    print(f"\nSweep done in {elapsed:.0f}s. {len(results)} valid configs.\n")

    rdf = pd.DataFrame(results)

    # Top 10 by total P&L
    top_pnl = rdf.nlargest(10, "total_pnl")
    print("=" * 90)
    print("🏆 Top 10 by Total P&L")
    print("=" * 90)
    print(top_pnl.to_string(index=False))

    # Top 10 by Sharpe
    top_sharpe = rdf.nlargest(10, "sharpe")
    print()
    print("=" * 90)
    print("📈 Top 10 by Sharpe")
    print("=" * 90)
    print(top_sharpe.to_string(index=False))

    # Top 10 by Profit Factor (exclude inf)
    finite_pf = rdf[rdf["profit_factor"] < 100]
    top_pf = finite_pf.nlargest(10, "profit_factor")
    print()
    print("=" * 90)
    print("💎 Top 10 by Profit Factor")
    print("=" * 90)
    print(top_pf.to_string(index=False))

    # Best positive configs
    positive = rdf[rdf["total_pnl"] > 0]
    print()
    print(f"✅ Positive P&L configs: {len(positive)} / {len(rdf)}")
    if len(positive) > 0:
        best = positive.nlargest(1, "total_pnl").iloc[0]
        print(f"   Best: TP={best.tp} SL={best.sl} Trail={best.trail} Hold={best.hold} TopN={best.topn}")
        print(f"   P&L={best.total_pnl:+,.0f}원 Sharpe={best.sharpe} WR={best.win_rate}% PF={best.profit_factor}")

    # Save
    out = ROOT / "reports" / "harness" / "param_sweep_results.json"
    out.write_text(json.dumps({
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "total_configs": len(rdf),
        "positive_configs": len(positive),
        "top10_by_pnl": top_pnl.to_dict("records"),
        "top10_by_sharpe": top_sharpe.to_dict("records"),
        "top10_by_pf": top_pf.to_dict("records"),
        "all_results": rdf.to_dict("records"),
    }, indent=2, ensure_ascii=False))
    print(f"\n💾 저장: {out}")


if __name__ == "__main__":
    run_sweep()
