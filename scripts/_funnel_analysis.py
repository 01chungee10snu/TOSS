#!/usr/bin/env python3
"""Funnel analysis: where do up_high_vol candidates die on recent panel dates?"""
import sys
sys.path.insert(0, "/Users/01chungee10/Github/TOSS/scripts")
sys.path.insert(0, "/Users/01chungee10/Github/TOSS/src")
import json
import pandas as pd
from generate_contextual_daily_candidates import prepare_features, load_policy
from pathlib import Path

ROOT = Path("/Users/01chungee10/Github/TOSS")
policy = load_policy(ROOT / "config/generated_policies/daily_multifactor_v1_practical400.json")
panel = pd.read_csv(policy["universe_source"], dtype={"code": str}, parse_dates=["Date"])
data = prepare_features(panel)

params = policy["situations"]["up_high_vol"]
print("template:", {k: params[k] for k in ("mode","min_mom_5d","max_mom_5d","max_rsi","min_vol_ratio","max_vol_ratio","max_vol_20d","min_dollar_volume","min_price","require_bb_lower","top_n")})

for as_of in ("2026-08-20","2026-08-21","2026-08-27","2026-08-28"):
    day = data[data["Date"] == as_of]
    if day.empty:
        print(as_of, "NO PANEL ROW"); continue
    sit = day["situation"].iloc[0]
    steps = {}
    base = day[pd.to_numeric(day["Close"], errors="coerce").notna()].copy()
    steps["universe_rows"] = len(base)
    f = base[pd.to_numeric(base["mom_5d"], errors="coerce").between(params["min_mom_5d"], params["max_mom_5d"])]
    steps["mom_band"] = len(f)
    f = f[pd.to_numeric(f["rsi_14"], errors="coerce") <= params["max_rsi"]]
    steps["rsi<=40"] = len(f)
    f = f[pd.to_numeric(f["vol_ratio"], errors="coerce").between(params["min_vol_ratio"], params["max_vol_ratio"])]
    steps["vol_ratio"] = len(f)
    f = f[pd.to_numeric(f["vol_20d"], errors="coerce") <= params["max_vol_20d"]]
    steps["vol_20d<=0.15"] = len(f)
    f = f[pd.to_numeric(f["dollar_volume"], errors="coerce") >= params["min_dollar_volume"]]
    steps["dollar_vol>=5e8"] = len(f)
    f = f[pd.to_numeric(f["Close"], errors="coerce") >= params["min_price"]]
    steps["price>=2000"] = len(f)
    if params.get("require_bb_lower"):
        f = f[pd.to_numeric(f["Close"], errors="coerce") < pd.to_numeric(f["bb_lower"], errors="coerce")]
        steps["bb_lower_break"] = len(f)
    print(f"{as_of} situation={sit} funnel={steps}")
    if steps.get("bb_lower_break", 0) == 0 and steps["dollar_vol>=5e8"] > 0:
        # what kills: show near-miss names (pass all except BB)
        near = base[pd.to_numeric(base["mom_5d"], errors="coerce").between(params["min_mom_5d"], params["max_mom_5d"])]
        near = near[pd.to_numeric(near["rsi_14"], errors="coerce") <= params["max_rsi"]]
        near = near[pd.to_numeric(near["dollar_volume"], errors="coerce") >= params["min_dollar_volume"]]
        cols = near[["code","name","Close","mom_5d","rsi_14","bb_lower","dollar_volume"]].head(8)
        print(cols.to_string(index=False))
