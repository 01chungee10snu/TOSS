#!/usr/bin/env python3
"""Write the current policy's live FIFO performance BUY gate and research queue."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from toss_alpha.execution.live_performance import (
    PerformanceThresholds,
    evaluate_live_performance,
    load_settlements,
    render_markdown,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "config/generated_policies/daily_multifactor_v1_practical400.json"
DEFAULT_SETTLEMENTS = ROOT / "reports/harness/settlements"
DEFAULT_JSON = ROOT / "reports/harness/live_performance_gate.json"
DEFAULT_MD = ROOT / "reports/harness/live_performance_gate.md"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=Path(os.environ.get("TOSS_POLICY_JSON", DEFAULT_POLICY)))
    parser.add_argument("--settlement-dir", type=Path, default=DEFAULT_SETTLEMENTS)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--md-out", type=Path, default=DEFAULT_MD)
    parser.add_argument("--deployed-since", default=os.environ.get("TOSS_LIVE_POLICY_DEPLOYED_SINCE", ""))
    parser.add_argument("--max-cumulative-loss-pct", type=float, default=float(os.environ.get("TOSS_LIVE_MAX_CUMULATIVE_LOSS_PCT", "0.03")))
    parser.add_argument("--max-consecutive-losing-fill-days", type=int, default=int(os.environ.get("TOSS_LIVE_MAX_CONSECUTIVE_LOSING_FILL_DAYS", "3")))
    parser.add_argument("--probation-settlement-days", type=int, default=int(os.environ.get("TOSS_LIVE_PROBATION_SETTLEMENT_DAYS", "20")))
    parser.add_argument("--probation-fills", type=int, default=int(os.environ.get("TOSS_LIVE_PROBATION_FILLS", "30")))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    policy_id = str(policy.get("policy_id") or args.policy.stem)
    created = str(policy.get("created_at_utc") or "")
    deployed_since = args.deployed_since or (created[:10] if len(created) >= 10 else datetime.now(timezone.utc).date().isoformat())
    thresholds = PerformanceThresholds(
        max_cumulative_loss_pct=args.max_cumulative_loss_pct,
        max_consecutive_losing_fill_days=args.max_consecutive_losing_fill_days,
        probation_settlement_days=args.probation_settlement_days,
        probation_fills=args.probation_fills,
    )
    payload = evaluate_live_performance(
        load_settlements(args.settlement_dir, deployed_since=deployed_since),
        policy_id=policy_id,
        deployed_since=deployed_since,
        thresholds=thresholds,
    )
    _atomic_write(args.json_out, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(args.md_out, render_markdown(payload))
    if not args.quiet:
        print(f"LIVE_PERFORMANCE_STATUS={payload['status']}")
        print(f"BLOCK_NEW_BUYS={str(payload['block_new_buys']).lower()}")
        print(f"CUMULATIVE_REALIZED_FIFO_KRW={payload['live_performance']['cumulative_realized_matched_fifo_krw']:.0f}")
        print(f"SETTLEMENT_DAYS={payload['sample']['settlement_days']}")
        print(f"FILL_COUNT={payload['sample']['fill_count']}")
        print(f"REPORT_JSON={args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
