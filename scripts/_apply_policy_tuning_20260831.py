#!/usr/bin/env python3
"""Apply evidence-backed policy update (2026-08-31).
Backtest evidence (PIT-correct, cost 24.5bps, 2022-02~2026-08):
- up_high_vol:   BB+h10+top3 avg +1.108%/trade (vs current BB+h7+top2 +0.551%), 2026 +2.47%
- flat_high_vol: BB+h10+top3 avg +1.773%/trade (vs +1.275%), 2026 +5.10%
- up_low_vol:    noBB+h10+top3 2026 +7.66%/trade n=66 (vs current BB+h7+top2 +4.27% n=36)
- flat_low_vol:  negative in all variants -> block live
- down_high_vol: 2026 -4.44%/trade -> stays blocked
"""
import json, shutil
from pathlib import Path

POLICY = Path("/Users/01chungee10/Github/TOSS/config/generated_policies/daily_multifactor_v1_practical400.json")
bak = POLICY.with_suffix(".json.bak-20260831-pre-tuning")
shutil.copy2(POLICY, bak)
p = json.loads(POLICY.read_text(encoding="utf-8"))

changes = {
    "up_high_vol":   {"top_n": 3, "require_bb_lower": True,  "return_col": "daily_10d_close"},
    "flat_high_vol": {"top_n": 3, "require_bb_lower": True,  "return_col": "daily_10d_close"},
    "up_low_vol":    {"top_n": 3, "require_bb_lower": False, "return_col": "daily_10d_close"},
}
for sit, upd in changes.items():
    s = p["situations"][sit]
    old = {k: s.get(k) for k in upd}
    s.update(upd)
    s["tuning_note"] = ("2026-08-31 PIT-correct sweep: hold 7->10d, top_n->3"
                        + (", BB filter removed" if not upd["require_bb_lower"] else ", BB kept"))
    print(f"{sit}: {old} -> {upd}")

p["tuning_20260831"] = {
    "basis": "scripts/_bb_filter_backtest.py, _bb_robustness.py, _bb_vs_hold_grid.py, _topn_test.py (PIT shift(1), 24.5bps)",
    "blocked_live_situations": ["down_high_vol", "flat_low_vol"],
}
POLICY.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
print("backup:", bak)
print("OK")
