#!/usr/bin/env python3
"""Walk-forward validation of best strategy configs.

Splits data into 6 rolling windows: 3-month IS + 1-month OOS each.
Tests whether the edge persists out-of-sample.

Best configs to validate:
1. oversold+RSI<30+BB+down/highvol, 7d, top2 (Sharpe 1.60)
2. oversold+RSI<30+BB, 7d, top2, SL8% (Sharpe 1.50)
3. oversold+RSI<30+BB+down/highvol, 5d, top2 (Sharpe 0.93)
"""
from __future__ import annotations
import json
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
    for d in range(1, 8):
        df[f"fwd_high_{d}d"] = df.groupby("code")["High"].shift(-d)
        df[fwd_low := f"fwd_low_{d}d"] = df.groupby("code")["Low"].shift(-d)
        df[f"fwd_close_{d}d"] = df.groupby("code")["Close"].shift(-d)
    mkt = df[df["code"] == KOSPI_PROXY][["Date", "ret_daily", "vol_20d"]].copy()
    mkt.columns = ["Date", "mkt_ret", "mkt_vol"]
    mkt["mkt_mom_5d"] = mkt["mkt_ret"].rolling(5).sum()
    df = df.merge(mkt, on="Date", how="left")
    return df


def sim_window(df_in, hold=7, topn=2, sl_pct=0.0, use_bb=True, use_regime=True, rsi_max=30):
    """Simulate one window. Returns (pnl, trades_list)."""
    valid = df_in.dropna(subset=["mom_5d", "dollar_volume", "rsi_14", "vol_20d", f"fwd_close_{hold}d", "mkt_ret"])
    valid = valid[valid["dollar_volume"] >= 5e8]

    mask = valid["mom_5d"] <= valid["mom_5d"].quantile(0.05)
    if rsi_max < 100:
        mask &= valid["rsi_14"] < rsi_max
    if use_bb:
        mask &= valid["Close"] < valid["bb_lower"]
    if use_regime:
        mask &= (valid["mkt_ret"] < 0.002) | (valid["mkt_vol"] > 0.015)

    cands = valid[mask]
    if cands.empty:
        return 0.0, 0, 0.0

    all_dates = sorted(cands["Date"].unique())
    if len(all_dates) < 5:
        return 0.0, 0, 0.0

    sl_price_factor = 1 - sl_pct
    daily_pnl = []
    n_trades = 0
    wins = 0

    for date in all_dates:
        day = cands[cands["Date"] == date]
        if day.empty:
            continue
        picks = day.nlargest(topn, "dollar_volume")
        day_pnl = 0
        for _, row in picks.iterrows():
            entry = row["Close"]
            exit_price = None
            for d in range(1, hold + 1):
                l = row.get(f"fwd_low_{d}d")
                c = row.get(f"fwd_close_{d}d")
                if pd.isna(l) or pd.isna(c):
                    break
                if sl_pct > 0 and l <= entry * sl_price_factor:
                    exit_price = entry * sl_price_factor
                    break
            if exit_price is None:
                fc = row.get(f"fwd_close_{hold}d")
                if pd.isna(fc) or fc <= 0:
                    continue
                exit_price = fc
            gross = exit_price / entry - 1
            net = gross - COST
            pnl = net * NOTIONAL
            day_pnl += pnl
            n_trades += 1
            if pnl > 0:
                wins += 1
        daily_pnl.append(day_pnl)

    if not daily_pnl:
        return 0.0, 0, 0.0

    pnl_series = pd.Series(daily_pnl)
    return pnl_series.sum(), n_trades, wins / max(n_trades, 1) * 100


def main():
    print("Loading data...")
    df = load()

    all_dates = sorted(df["Date"].unique())
    # Remove warmup period
    start_idx = 30
    all_dates = all_dates[start_idx:]
    print(f"Period: {all_dates[0].date()} → {all_dates[-1].date()}")
    print()

    # Walk-forward: 6-month IS, 3-month OOS, rolling
    is_days = 126   # ~6 months
    oos_days = 63   # ~3 months
    stride = is_days + oos_days

    configs = [
        {"name": "BEST: BB+RSI30+regime, 7d, top2", "hold": 7, "topn": 2, "sl": 0.0, "use_bb": True, "use_regime": True, "rsi_max": 30},
        {"name": "BB+RSI30+regime, 7d, top2, SL8%", "hold": 7, "topn": 2, "sl": 0.08, "use_bb": True, "use_regime": True, "rsi_max": 30},
        {"name": "BB+RSI30, 7d, top2 (no regime)", "hold": 7, "topn": 2, "sl": 0.0, "use_bb": True, "use_regime": False, "rsi_max": 30},
        {"name": "BB+RSI25+regime, 7d, top2", "hold": 7, "topn": 2, "sl": 0.0, "use_bb": True, "use_regime": True, "rsi_max": 25},
        {"name": "BB+RSI30+regime, 5d, top2", "hold": 5, "topn": 2, "sl": 0.0, "use_bb": True, "use_regime": True, "rsi_max": 30},
    ]

    n_windows = (len(all_dates) - is_days) // oos_days
    print(f"Walk-forward: {n_windows} windows, IS={is_days}d, OOS={oos_days}d\n")

    all_results = {}

    for cfg in configs:
        print("=" * 95)
        print(f"  {cfg['name']}")
        print("=" * 95)
        print(f"  {'Window':<20} {'IS Period':<28} {'OOS Period':<28} {'IS P&L':>10} {'OOS P&L':>10} {'OOS WR':>7} {'OOS N':>6}")
        print("  " + "-" * 115)

        oos_pnls = []
        oos_trades = []
        oos_wrs = []

        for w in range(n_windows):
            is_start = w * oos_days
            is_end = is_start + is_days
            oos_start = is_end
            oos_end = oos_start + oos_days

            if oos_end > len(all_dates):
                break

            is_dates = all_dates[is_start:is_end]
            oos_dates = all_dates[oos_start:oos_end]

            is_df = df[df["Date"].isin(is_dates)]
            oos_df = df[df["Date"].isin(oos_dates)]

            # IS: also compute the quantile threshold from IS data
            is_pnl, is_n, is_wr = sim_window(
                is_df, hold=cfg["hold"], topn=cfg["topn"], sl_pct=cfg["sl"],
                use_bb=cfg["use_bb"], use_regime=cfg["use_regime"], rsi_max=cfg["rsi_max"]
            )

            # OOS: use the SAME strategy params (no re-optimization)
            oos_pnl, oos_n, oos_wr = sim_window(
                oos_df, hold=cfg["hold"], topn=cfg["topn"], sl_pct=cfg["sl"],
                use_bb=cfg["use_bb"], use_regime=cfg["use_regime"], rsi_max=cfg["rsi_max"]
            )

            oos_pnls.append(oos_pnl)
            oos_trades.append(oos_n)
            oos_wrs.append(oos_wr)

            is_label = f"{is_dates[0].date()}~{is_dates[-1].date()}"
            oos_label = f"{oos_dates[0].date()}~{oos_dates[-1].date()}"

            print(f"  W{w+1:<18} {is_label:<28} {oos_label:<28} {is_pnl:>+9,.0f}원 {oos_pnl:>+9,.0f}원 {oos_wr:>6.1f}% {oos_n:>6}")

        total_oos = sum(oos_pnls)
        total_trades = sum(oos_trades)
        avg_wr = np.mean(oos_wrs) if oos_wrs else 0
        pos_windows = sum(1 for p in oos_pnls if p > 0)

        print()
        print(f"  📊 OOS TOTAL: P&L={total_oos:+,.0f}원  Trades={total_trades}  WR={avg_wr:.1f}%  Positive windows={pos_windows}/{len(oos_pnls)}")
        print()

        all_results[cfg["name"]] = {
            "oos_total_pnl": round(total_oos, 0),
            "oos_total_trades": total_trades,
            "oos_avg_wr": round(avg_wr, 1),
            "positive_windows": pos_windows,
            "total_windows": len(oos_pnls),
            "window_details": [
                {"window": w+1, "oos_pnl": round(oos_pnls[w], 0), "oos_trades": oos_trades[w], "oos_wr": round(oos_wrs[w], 1)}
                for w in range(len(oos_pnls))
            ],
        }

    # Summary
    print()
    print("=" * 95)
    print("WALK-FORWARD SUMMARY")
    print("=" * 95)
    print(f"  {'Config':<45} {'OOS P&L':>12} {'Trades':>8} {'WR%':>6} {'Win Win':>8}")
    print("  " + "-" * 85)
    for name, r in all_results.items():
        print(f"  {name:<43} {r['oos_total_pnl']:>+10,.0f}원 {r['oos_total_trades']:>8} {r['oos_avg_wr']:>5.1f}% {r['positive_windows']:>3}/{r['total_windows']:<3}")

    out = ROOT / "reports" / "harness" / "walkforward_v2_results.json"
    out.write_text(json.dumps({
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "method": "rolling 6m IS + 3m OOS, no re-optimization",
        "results": all_results,
    }, indent=2, ensure_ascii=False))
    print(f"\n💾 저장: {out}")


if __name__ == "__main__":
    main()
