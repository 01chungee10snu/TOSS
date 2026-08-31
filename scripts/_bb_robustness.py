#!/usr/bin/env python3
"""Year-split robustness for candidate variants + all-situation impact check."""
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
data["next_open"] = g["Open"].shift(-1)
for h in (7, 10):
    data[f"fwd_{h}d"] = g["Close"].shift(-1 - h)

COST = 0.00245

def sim(situation, require_bb, bb_buffer, hold, top_n=2, data=data):
    p = dict(policy["situations"][situation])
    trades = []
    for date, day in data.groupby("Date"):
        if day["situation"].iloc[0] != situation:
            continue
        f = day[pd.to_numeric(day["Close"], errors="coerce").notna()].copy()
        f = f[pd.to_numeric(f["mom_5d"], errors="coerce").between(p["min_mom_5d"], p["max_mom_5d"])]
        f = f[pd.to_numeric(f["rsi_14"], errors="coerce") <= p["max_rsi"]]
        f = f[pd.to_numeric(f["vol_ratio"], errors="coerce").between(p["min_vol_ratio"], p["max_vol_ratio"])]
        f = f[pd.to_numeric(f["vol_20d"], errors="coerce").between(0, p["max_vol_20d"])]
        f = f[pd.to_numeric(f["dollar_volume"], errors="coerce") >= p["min_dollar_volume"]]
        f = f[pd.to_numeric(f["Close"], errors="coerce") >= p["min_price"]]
        if require_bb:
            f = f[pd.to_numeric(f["Close"], errors="coerce") < pd.to_numeric(f["bb_lower"], errors="coerce") * (1 + bb_buffer)]
        if f.empty:
            continue
        f = f.copy()
        f["score"] = _score_multi(f, p)
        f = f.nsmallest(top_n, "score")
        for _, r in f.iterrows():
            entry, exitp = r["next_open"], r.get(f"fwd_{hold}d")
            if pd.isna(entry) or pd.isna(exitp) or entry <= 0:
                continue
            trades.append({"date": date, "ret": exitp / entry - 1 - COST})
    return pd.DataFrame(trades)

def report(label, t):
    if t.empty or len(t) == 0:
        print(f"  {label}: 0 trades"); return
    by = t.groupby(t["date"].dt.year)["ret"]
    parts = []
    for y, s in by:
        wr = (s > 0).mean() * 100
        parts.append(f"{y}: n={len(s):3d} avg={s.mean():+.2%} wr={wr:.0f}%")
    tot = t["ret"].sum()
    print(f"  {label}: TOTAL n={len(t)} avg={t['ret'].mean():+.3%} sum={tot:+.0%}")
    print("    " + " | ".join(parts))

print("== A) up_high_vol: BB+5% buffer, hold7 vs no-BB hold10 — year split ==")
report("BB+5% h7", sim("up_high_vol", True, 0.05, 7))
report("no-BB  h10", sim("up_high_vol", False, 0, 10))
print()
print("== B) all situations with no-BB hold10 (candidate for new default) ==")
for sit in ("up_low_vol", "flat_low_vol", "down_high_vol", "up_high_vol", "flat_high_vol"):
    report(f"{sit:14s}", sim(sit, False, 0, 10))
print()
print("== C) all situations CURRENT policy (BB, h7) for comparison ==")
for sit in ("up_low_vol", "flat_low_vol", "down_high_vol", "up_high_vol", "flat_high_vol"):
    report(f"{sit:14s}", sim(sit, True, 0.0, 7))
