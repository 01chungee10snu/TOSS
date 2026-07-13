#!/usr/bin/env python3
"""Audit the strategic live-decision harness invariants.

This script does not modify source code or submit orders. It checks whether the
current TOSS repo contains the required gate scripts, integration points,
current issue report, and cron-facing wrappers. It emits a concise report and
writes JSON audit evidence.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
KST = ZoneInfo("Asia/Seoul")
OUT = ROOT / "reports" / "harness" / "strategic_live_decision_harness_audit.json"

REQUIRED_FILES = [
    ".openclaw/harness/references/strategic-live-decision-harness.md",
    "scripts/current_issue_risk_report.py",
    "scripts/current_issue_risk_report.sh",
    "scripts/risk_off_inverse_entry_20260708.py",
    "scripts/risk_off_inverse_entry_20260708.sh",
    "scripts/rebound_open_detector_20260708.py",
    "scripts/rebound_open_detector_20260708.sh",
    "scripts/rebound_exit_watchdog_20260708.py",
    "scripts/rebound_exit_watchdog_20260708.sh",
    "src/toss_alpha/execution/live_submit.py",
    "src/toss_alpha/execution/position_exit.py",
]

REQUIRED_PATTERNS = {
    "src/toss_alpha/execution/live_submit.py": [
        r"def current_issue_buy_violation",
        r"TOSS_ALLOW_CURRENT_ISSUE_BUY",
        r"current_issue_buy_block",
        r"issue_violation = current_issue_buy_violation",
        r"def strategic_harness_audit_buy_violation",
        r"harness_violation = strategic_harness_audit_buy_violation",
    ],
    "src/toss_alpha/execution/position_exit.py": [
        r"def position_quote_invalid_reason",
        r"invalid_reason = position_quote_invalid_reason",
        r"def block_buys_for_position_quote_errors",
        r"base_payload = block_buys_for_position_quote_errors",
        r"buy_block_reasons.append\(\"position_exit_quote_unavailable\"\)",
    ],
    "scripts/current_issue_risk_report.py": [
        r"Broad market mood",
        r"classify\(headlines: list\[dict\].*lookback_hours",
        r"text = str\(row.get\(\"title\"",
        r"categorize_title",
        r"category_counts",
        r"parse_pubdate",
        r"buy_gate",
    ],
    "scripts/risk_off_inverse_entry_20260708.py": [
        r"ETF_CODE = \"252670\"",
        r"inverse_sleeve_current_issue_20260708",
        r"run_live_submit_phase",
    ],
    "scripts/rebound_open_detector_20260708.py": [
        r"current_issue_buy_violation",
        r"MIN_REBOUND_FROM_LOW",
        r"run_live_submit_phase",
    ],
    "scripts/rebound_exit_watchdog_20260708.py": [
        r"STOP_LOSS_PCT",
        r"TAKE_PROFIT_PCT",
        r"run_live_submit_phase",
    ],
}


def check_files() -> list[dict]:
    rows = []
    for rel in REQUIRED_FILES:
        path = ROOT / rel
        rows.append({"check": "file_exists", "path": rel, "ok": path.exists()})
    return rows


def check_patterns() -> list[dict]:
    rows = []
    for rel, patterns in REQUIRED_PATTERNS.items():
        path = ROOT / rel
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        for pattern in patterns:
            rows.append({"check": "pattern", "path": rel, "pattern": pattern, "ok": bool(re.search(pattern, text, flags=re.S))})
    return rows


def check_current_issue_report() -> list[dict]:
    today = datetime.now(timezone.utc).astimezone(KST).strftime("%Y%m%d")
    path = ROOT / "reports" / "harness" / "current_issues" / f"current_issue_risk_report_{today}.json"
    if not path.exists():
        return [{"check": "current_issue_report_today", "path": str(path), "ok": False, "reason": "missing"}]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [{"check": "current_issue_report_today", "path": str(path), "ok": False, "reason": repr(exc)}]
    rows = [
        {"check": "current_issue_report_today", "path": str(path), "ok": True},
        {"check": "current_issue_has_severity", "ok": payload.get("severity") in {"low", "medium", "high", "critical"}, "value": payload.get("severity")},
        {"check": "current_issue_has_buy_gate", "ok": payload.get("buy_gate") in {"allow", "allow_with_caution", "block_new_buy"}, "value": payload.get("buy_gate")},
        {"check": "current_issue_uses_recency_counts", "ok": "considered_headline_count" in payload and "stale_headline_count" in payload},
    ]
    return rows


def main() -> int:
    checks = check_files() + check_patterns() + check_current_issue_report()
    ok = all(row.get("ok") for row in checks)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if ok else "FAIL",
        "checks": checks,
        "failures": [row for row in checks if not row.get("ok")],
        "known_remaining_work": [
            "Generalize 20260708-specific scripts into date/config-driven reusable scripts.",
            "Add inverse ETF exit watchdog for 252670/251340 positions.",
            "Blend current-issue RSS classifier with market data: WTI, USDKRW, VIX, KOSPI/KOSDAQ futures.",
            "Add fill reconciliation before exit watchdog relies on position state.",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"HARNESS_AUDIT_STATUS={payload['status']}")
    print(f"CHECKS={len(checks)} FAILURES={len(payload['failures'])}")
    print(f"WROTE={OUT}")
    for failure in payload["failures"][:10]:
        print("FAIL", failure)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
