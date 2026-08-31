#!/usr/bin/env python3
"""FINAL CONFIG validation: recent 4 months — how often would the new config trade
and what would P&L be? Config: up_low_vol(noBB,h10) + up_high_vol(BB,h10) +
flat_high_vol(BB,h10); flat_low_vol & down_high_vol blocked."""
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

CONFIG = {  # situation -> (require_bb, live)
    "up_low_vol": (False, True),
    "up_high_vol": (True, True),
    "flat_high_vol": (True, True),
    "flat_low_vol": (True, False),   # blocked
    "down_high_vol": (True, False),  # blocked
}

trades = []
for date, day in data.groupby("Date"):
    sit = day["situation"].iloc[0]
    cfg = CONFIG.get(sit)
    if not cfg or not cfg[1]:
        continue
    require_bb = cfg[0]
    p = dict(policy["situations"][sit])
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
    f = f.nsmallest(2, "score")
    for _, r in f.iterrows():
        entry, exitp = r["next_open"], r["fwd_10d"]
        if pd.isna(entry) or pd.isna(exitp) or entry <= 0:
            continue
        trades.append({"date": date, "sit": sit, "code": r["code"], "name": r.get("name", ""), "ret": exitp / entry - 1 - COST})

t = pd.DataFrame(trades)
recent = t[t["date"] >= "2026-05-01"]
print(f"FULL: n={len(t)} avg={t['ret'].mean():+.3%} sum={t['ret'].sum():+.0%}")
print(f"\nRECENT (since 2026-05-01): n={len(recent)} trading days with trades={recent['date'].nunique()}")
if not recent.empty:
    print(recent.groupby("sit").agg(n=("ret", "size"), avg=("ret", "mean"), sum=("ret", "sum")))
    print("\nLast 12 trades:")
    print(recent.tail(12)[["date", "sit", "code", "name", "ret"]].to_string(index=False))
# situation day distribution last 4 months
recent_days = data[data["Date"] >= "2026-05-01"].groupby("Date")["situation"].first()
print("\nSituation day counts since 2026-05-01:")
print(recent_days.value_counts())
