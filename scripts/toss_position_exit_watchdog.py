#!/usr/bin/env python3
"""Silent sell-only position watchdog for the TOSS/KIS live account."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from toss_alpha.execution.live_ready import live_readiness
from toss_alpha.execution.live_submit import korea_regular_market_violation, run_live_submit_phase
from toss_alpha.execution.position_exit import append_position_exit_orders

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "harness"
LATEST_LOOP = REPORT_DIR / "latest_loop_report.json"
KST = ZoneInfo("Asia/Seoul")


def load_recent_intraday_decision(now: datetime) -> dict | None:
    try:
        report = json.loads(LATEST_LOOP.read_text(encoding="utf-8"))
        decision = (report.get("intraday") or {}).get("decision")
        if not isinstance(decision, dict):
            return None
        generated = datetime.fromisoformat(str(decision.get("generated_at_utc") or "").replace("Z", "+00:00"))
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        max_age = int(os.environ.get("TOSS_INTRADAY_MAX_QUOTE_AGE_SECONDS", "300"))
        age = (now - generated.astimezone(timezone.utc)).total_seconds()
        return decision if -30 <= age <= max_age else None
    except Exception:
        return None


def main() -> int:
    now = datetime.now(timezone.utc)
    if korea_regular_market_violation(now, env=os.environ):
        return 0
    today = now.astimezone(KST).date().isoformat()
    candidate = {
        "generated_at_utc": now.isoformat(),
        "as_of": today,
        "status": "NO_TRADE",
        "policy_id": "sell_only_position_watchdog_v1",
        "strategy_type": "sell_only_position_watchdog",
        "situation": "watchdog_sell_only",
        "orders": [],
    }
    decision = load_recent_intraday_decision(now)
    if decision is not None:
        candidate["intraday_decision"] = decision
    merged, audit = append_position_exit_orders(candidate, report_dir=REPORT_DIR, env=os.environ)
    orders = [order for order in (merged.get("orders") or []) if str(order.get("side") or "").upper() == "SELL"]
    if not orders:
        if audit.get("reason") in {None, "no_positions"}:
            return 0
        if audit.get("reason") == "position_exit_exception":
            print(f"BLOCKED: position exit state unavailable ({audit.get('exception_type')}:{audit.get('exception')})")
        return 0
    payload = dict(merged)
    payload["orders"] = orders
    payload["status"] = "CANDIDATES"
    payload["strategy_type"] = "sell_only_position_watchdog"
    qual = {"status": "READY", "reasons": [], "checked_symbols": [str(o.get("symbol")) for o in orders]}
    result = run_live_submit_phase(
        candidate_payload=payload,
        qual=qual,
        live=live_readiness(),
        report_dir=REPORT_DIR,
        env=os.environ,
        now=now,
    )
    submitted = int(result.get("submitted_count") or 0)
    blocked = int(result.get("blocked_count") or 0)
    if submitted or blocked:
        print(json.dumps({
            "watchdog": "sell_only_position_watchdog_v1",
            "generated_at_kst": now.astimezone(KST).isoformat(),
            "submitted_count": submitted,
            "blocked_count": blocked,
            "orders": [{"symbol": o.get("symbol"), "quantity": o.get("quantity"), "reason": o.get("reason")} for o in orders],
            "artifact_path": result.get("artifact_path"),
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
