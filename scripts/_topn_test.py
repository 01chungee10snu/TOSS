#!/usr/bin/env python3
"""top_n=3 test: does adding a 3rd pick keep per-trade edge? Config: BB kept, h10."""
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
data["fwd_10d"] = g["Close"].shift(-11)
COST = 0.00245

def sim(situation, require_bb, hold, top_n):
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
            f = f[pd.to_numeric(f["Close"], errors="coerce") < pd.to_numeric(f["bb_lower"], errors="coerce")]
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
    if t.empty:
        print(f"  {label}: 0 trades"); return
    by = t.groupby(t["date"].dt.year)["ret"]
    parts = [f"{y}: {s.mean():+.2%}(n={len(s)})" for y, s in by]
    print(f"  {label}: avg={t['ret'].mean():+.3%} n={len(t)}")
    print("     " + " | ".join(parts))

for sit, rbb in (("up_high_vol", True), ("flat_high_vol", True), ("up_low_vol", False)):
    print(f"== {sit} (BB={rbb}, h10) ==")
    report("top2", sim(sit, rbb, 10, 2))
    report("top3", sim(sit, rbb, 10, 3))
    print()
