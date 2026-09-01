#!/usr/bin/env python3
"""Round 2: vol_ratio floor sweet spot + combos, up_high_vol & flat_high_vol."""
import sys
sys.path.insert(0, "/Users/01chungee10/Github/TOSS/scripts")
sys.path.insert(0, "/Users/01chungee10/Github/TOSS/src")
import pandas as pd
from generate_contextual_daily_candidates import prepare_features, load_policy, _score_multi
from pathlib import Path

ROOT = Path("/Users/01chungee10/Github/TOSS")
policy = load_policy(ROOT / "config/generated_policies/daily_multifactor_v1_practical400.json")
panel = pd.read_csv(policy["universe_source"], dtype={"code": str}, parse_dates=["Date"])
data = prepare_features(panel)
data = data.sort_values(["code", "Date"])
g = data.groupby("code", group_keys=False)
data["fwd_10d"] = g["Close"].shift(-11)
COST = 0.00245

def sim(situation, overrides, hold=10, top_n=3, years=None):
    p = dict(policy["situations"][situation]); p.update(overrides)
    trades = []
    sig_days = 0; tot_days = 0
    for date, day in data.groupby("Date"):
        if day["situation"].iloc[0] != situation: continue
        if years and date.year not in years: continue
        tot_days += 1
        f = day[pd.to_numeric(day["Close"], errors="coerce").notna()].copy()
        f = f[pd.to_numeric(f["mom_5d"], errors="coerce").between(p["min_mom_5d"], p["max_mom_5d"])]
        f = f[pd.to_numeric(f["rsi_14"], errors="coerce") <= p["max_rsi"]]
        f = f[pd.to_numeric(f["vol_ratio"], errors="coerce").between(p["min_vol_ratio"], p["max_vol_ratio"])]
        f = f[pd.to_numeric(f["vol_20d"], errors="coerce").between(0, p["max_vol_20d"])]
        f = f[pd.to_numeric(f["dollar_volume"], errors="coerce") >= p["min_dollar_volume"]]
        f = f[pd.to_numeric(f["Close"], errors="coerce") >= p["min_price"]]
        if p.get("require_bb_lower"):
            c = pd.to_numeric(f["Close"], errors="coerce"); b = pd.to_numeric(f["bb_lower"], errors="coerce")
            f = f[c < b]
        if f.empty: continue
        sig_days += 1
        f = f.copy(); f["score"] = _score_multi(f, p)
        f = f.nsmallest(top_n, "score")
        for _, r in f.iterrows():
            fwd = r.get(f"fwd_{hold}d")
            if pd.notna(fwd):
                trades.append((r["Date"].year, (float(fwd)/float(r["Close"]) - 1) - COST))
    return trades, sig_days, tot_days

VARIANTS = {
    "base(0.8)":       {},
    "floor 0.7":       {"min_vol_ratio": 0.7},
    "floor 0.6":       {"min_vol_ratio": 0.6},
    "floor 0.5":       {"min_vol_ratio": 0.5},
    "floor 0.4":       {"min_vol_ratio": 0.4},
    "floor0.5+RSI45":  {"min_vol_ratio": 0.5, "max_rsi": 45},
    "floor0.5+dv3억":  {"min_vol_ratio": 0.5, "min_dollar_volume": 300_000_000},
}

for sit in ["up_high_vol", "flat_high_vol"]:
    print(f"== {sit} (BB hard, h10, top3) ==")
    print(f"{'변형':16s} {'avg/건':>8s} {'2026/건':>8s} {'25-26/건':>9s} {'n':>5s} {'신호일%':>7s}")
    for name, ov in VARIANTS.items():
        trades, sig, tot = sim(sit, ov)
        if not trades: print(f"{name:16s}  거래없음"); continue
        df = pd.DataFrame(trades, columns=["year", "ret"])
        y26 = df[df["year"] == 2026]["ret"]
        y2526 = df[df["year"] >= 2025]["ret"]
        print(f"{name:16s} {df['ret'].mean()*100:+7.3f}% {y26.mean()*100 if len(y26) else float('nan'):+7.2f}% {y2526.mean()*100:+8.2f}% {len(df):5d} {sig/tot*100:6.0f}%")
    print()
