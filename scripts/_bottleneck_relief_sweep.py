#!/usr/bin/env python3
"""Bottleneck relief sweep for up_high_vol: keep BB hard, widen upstream filters.

Measures per-trade avg (overall + 2026) and signal-day coverage for each
variant. BB lower-break stays ON (strongest per-trade edge: +1.37%).
"""
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

VARIANTS = {
    "base(현재)":        {},
    "vol_ratio上限8":     {"max_vol_ratio": 8.0},
    "vol_ratio上限12":    {"max_vol_ratio": 12.0},
    "vol_ratio下限0.5":   {"min_vol_ratio": 0.5},
    "거래대금3억":        {"min_dollar_volume": 300_000_000},
    "거래대금2억":        {"min_dollar_volume": 200_000_000},
    "거래대금1억":        {"min_dollar_volume": 100_000_000},
    "RSI45":             {"max_rsi": 45},
    "mom_max-0.015":     {"max_mom_5d": -0.015},
    "mom_min-0.45":      {"min_mom_5d": -0.45},
}

def sim(situation, overrides, hold=10, top_n=3):
    p = dict(policy["situations"][situation]); p.update(overrides)
    trades = []
    sig_days = 0; tot_days = 0
    for date, day in data.groupby("Date"):
        if day["situation"].iloc[0] != situation: continue
        tot_days += 1
        f = day[pd.to_numeric(day["Close"], errors="coerce").notna()].copy()
        f = f[pd.to_numeric(f["mom_5d"], errors="coerce").between(p["min_mom_5d"], p["max_mom_5d"])]
        f = f[pd.to_numeric(f["rsi_14"], errors="coerce") <= p["max_rsi"]]
        f = f[pd.to_numeric(f["vol_ratio"], errors="coerce").between(p["min_vol_ratio"], p["max_vol_ratio"])]
        f = f[pd.to_numeric(f["vol_20d"], errors="coerce").between(0, p["max_vol_20d"])]
        f = f[pd.to_numeric(f["dollar_volume"], errors="coerce") >= p["min_dollar_volume"]]
        f = f[pd.to_numeric(f["Close"], errors="coerce") >= p["min_price"]]
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

# which vol_ratio bound binds (last panel day, pre-BB funnel)
day = data[data["Date"] == data["Date"].max()]
day = day[day["situation"] == "up_high_vol"]
p = policy["situations"]["up_high_vol"]
f = day[pd.to_numeric(day["mom_5d"], errors="coerce").between(p["min_mom_5d"], p["max_mom_5d"])]
f = f[pd.to_numeric(f["rsi_14"], errors="coerce") <= p["max_rsi"]]
vr = pd.to_numeric(f["vol_ratio"], errors="coerce")
print(f"[진단] RSI통과 {len(f)}종목 중 vol_ratio>5: {(vr>5).sum()}개, vol_ratio<0.8: {(vr<0.8).sum()}개, 0.8~5: {vr.between(0.8,5).sum()}개")
print(f"[진단] vol_ratio 분포: min={vr.min():.2f} p25={vr.quantile(.25):.2f} med={vr.median():.2f} p75={vr.quantile(.75):.2f} max={vr.max():.2f}")
print()

print(f"{'변형':18s} {'avg/건':>8s} {'2026/건':>8s} {'n':>5s} {'신호일%':>7s}")
for name, ov in VARIANTS.items():
    trades, sig, tot = sim("up_high_vol", ov)
    if not trades:
        print(f"{name:18s}  거래없음"); continue
    df = pd.DataFrame(trades, columns=["year", "ret"])
    y26 = df[df["year"] == 2026]["ret"]
    print(f"{name:18s} {df['ret'].mean()*100:+7.3f}% {y26.mean()*100 if len(y26) else float('nan'):+7.2f}% {len(df):5d} {sig/tot*100:6.0f}%")
