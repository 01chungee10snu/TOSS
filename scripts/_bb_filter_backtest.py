#!/usr/bin/env python3
"""PIT-correct backtest: does dropping require_bb_lower (or relaxing it) keep the
up_high_vol reversal profitable? Simulate exactly like the funnel: PIT factors
(already shift(1)-based in prepare_features), entry next open, exit 7d close."""
import sys
sys.path.insert(0, "/Users/01chungee10/Github/TOSS/scripts")
sys.path.insert(0, "/Users/01chungee10/Github/TOSS/src")
import json
import numpy as np
import pandas as pd
from generate_contextual_daily_candidates import prepare_features, load_policy, score, _score_multi
from pathlib import Path

ROOT = Path("/Users/01chungee10/Github/TOSS")
policy = load_policy(ROOT / "config/generated_policies/daily_multifactor_v1_practical400.json")
panel = pd.read_csv(policy["universe_source"], dtype={"code": str}, parse_dates=["Date"])
data = prepare_features(panel)

params = policy["situations"]["up_high_vol"]
# future returns, PIT-safe: entry at NEXT day open after signal date t
data = data.sort_values(["code", "Date"])
g = data.groupby("code", group_keys=False)
data["next_open"] = g["Open"].shift(-1)
for h in (5, 7, 10):
    data[f"fwd_close_{h}d"] = g["Close"].shift(-1 - h)  # close at t+h (entry next open)
data["fwd_ret_7d_open_open"] = data["next_open"].pipe(lambda s: s) / data["Close"] - 0  # placeholder

def run(label, require_bb, bb_buffer=0.0, top_n=None, hold=7):
    p = dict(params)
    p["require_bb_lower"] = require_bb
    trades = []
    for date, day in data.groupby("Date"):
        sit = day["situation"].iloc[0] if "situation" in day else None
        if sit != "up_high_vol":
            continue
        base = day[pd.to_numeric(day["Close"], errors="coerce").notna()].copy()
        f = base[pd.to_numeric(base["mom_5d"], errors="coerce").between(p["min_mom_5d"], p["max_mom_5d"])]
        f = f[pd.to_numeric(f["rsi_14"], errors="coerce") <= p["max_rsi"]]
        f = f[pd.to_numeric(f["vol_ratio"], errors="coerce").between(p["min_vol_ratio"], p["max_vol_ratio"])]
        f = f[pd.to_numeric(f["vol_20d"], errors="coerce").between(0, p["max_vol_20d"])]
        f = f[pd.to_numeric(f["dollar_volume"], errors="coerce") >= p["min_dollar_volume"]]
        f = f[pd.to_numeric(f["Close"], errors="coerce") >= p["min_price"]]
        if p.get("require_bb_lower"):
            f = f[pd.to_numeric(f["Close"], errors="coerce") < pd.to_numeric(f["bb_lower"], errors="coerce") * (1 + bb_buffer)]
        if f.empty:
            continue
        f = f.copy()
        f["score"] = _score_multi(f, p)
        f = f.nsmallest(top_n or p["top_n"], "score")
        for _, r in f.iterrows():
            entry = r["next_open"]
            exitp = r.get(f"fwd_close_{hold}d")
            if pd.isna(entry) or pd.isna(exitp) or entry <= 0:
                continue
            trades.append({"date": date, "code": r["code"], "name": r.get("name", ""), "entry": entry, "exit": exitp, "ret": exitp / entry - 1})
    t = pd.DataFrame(trades)
    if t.empty:
        print(f"{label}: 0 trades")
        return t
    cost = 0.00245
    t["net"] = t["ret"] - cost
    wr = (t["net"] > 0).mean() * 100
    tot = t["net"].sum()
    avg = t["net"].mean()
    pf = t.loc[t["net"] > 0, "net"].sum() / max(1e-9, -t.loc[t["net"] < 0, "net"].sum())
    print(f"{label}: trades={len(t)} win_rate={wr:.1f}% total_net={tot:+.2%} avg={avg:+.3%} PF={pf:.2f} span={t['date'].min().date()}~{t['date'].max().date()}")
    return t

print("== up_high_vol reversal, hold=7d, top2, cost 24.5bps ==")
t_base = run("BB-lower required (current)", True)
t_none = run("no BB filter              ", False)
t_buf  = run("BB with +2% buffer        ", True, bb_buffer=0.02)
t_buf5 = run("BB with +5% buffer        ", True, bb_buffer=0.05)

# Also check hold=5 and hold=10 for the no-BB variant
print()
run("no BB, hold=5d", False, hold=5)
run("no BB, hold=10d", False, hold=10)
