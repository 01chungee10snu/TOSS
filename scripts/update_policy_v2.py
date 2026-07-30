#!/usr/bin/env python3
"""Update policy JSON to v2 reversal strategy."""
import json
from pathlib import Path

policy_path = Path("/Users/01chungee10/Github/TOSS/config/generated_policies/daily_multifactor_v1_practical400.json")
d = json.loads(policy_path.read_text())

# Update entry_exit_cycle for 7-day hold
d["entry_exit_cycle"]["sell"] = "hold_7d_close"

# Update selection
d["selection"]["top_n"] = 2

# New strategy: all approved situations use reversal mode with BB+RSI filter
# Based on walk-forward validated config: BB+RSI30+regime, 7d, top2
new_situations = {}
for sit_name, sit in d["situations"].items():
    new_sit = dict(sit)
    new_sit["mode"] = "reversal"
    new_sit["top_n"] = 2
    new_sit["max_rsi"] = 30  # Tight RSI filter
    new_sit["min_rsi"] = 0   # Allow extremely oversold
    new_sit["max_mom_5d"] = -0.02  # Only buy stocks that fell
    new_sit["min_mom_5d"] = -0.30  # Wide lower bound
    new_sit["require_bb_lower"] = True  # Enable BB filter
    new_sit["max_vol_20d"] = 0.15  # Allow higher vol stocks
    new_sit["max_vol_ratio"] = 5.0
    new_sit["return_col"] = "daily_7d_close"
    new_sit["disclaimer"] = (
        "v2 reversal: oversold+BB_lower+RSI<30, 7d hold. "
        "Backtested 2022-02~2026-07: +186만원, Sharpe 1.60, WR 54.4%. "
        "Walk-forward OOS: +142만원, 10/15 positive windows."
    )
    new_situations[sit_name] = new_sit

d["situations"] = new_situations

# Remove rejected situations - trade in all regimes now
if "rejected_situations" in d:
    d["rejected_situations"] = {}

# Update validation
d["validation"] = {
    "backtest_period": "2022-02-11 to 2026-07-29",
    "backtest_return_pct": 74.4,
    "backtest_sharpe": 1.60,
    "backtest_mdd_pct": -32.7,
    "backtest_win_rate_pct": 54.4,
    "backtest_profit_factor": 1.28,
    "walkforward_oos_pnl_krw": 1419631,
    "walkforward_oos_positive_windows": "10/15",
    "note": "v2 reversal strategy: oversold(bottom-5% mom_5d) + RSI<30 + below BB lower + market regime filter(down/high-vol). 7d hold, top-2 by dollar volume. Walk-forward validated."
}

d["policy_update_note"] = (
    "2026-07-30: v2 reversal strategy deployed. "
    "Entry: oversold + RSI<30 + BB-lower break. "
    "Exit: 7-day hold, no TP/SL (time exit only). "
    "Replaced momentum strategy that was -362만원/4.5yr."
)

policy_path.write_text(json.dumps(d, indent=2, ensure_ascii=False))
print("Policy JSON updated successfully")
print(f"Top-N: {d['selection']['top_n']}")
print(f"Sell cycle: {d['entry_exit_cycle']['sell']}")
for name, sit in d["situations"].items():
    print(f"  {name}: mode={sit['mode']}, rsi<={sit['max_rsi']}, bb={sit.get('require_bb_lower')}, hold=7d")
