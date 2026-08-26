#!/usr/bin/env python3
"""Generate the latest adaptive-regime shadow plan from audited artifacts.

Research/shadow only. This runner cannot submit broker orders.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from toss_alpha.execution.adaptive_regime_router import build_shadow_plan

HARNESS = ROOT / "reports" / "harness"
DEFAULT_LOOP = HARNESS / "latest_loop_report.json"
DEFAULT_FORWARD_DIR = HARNESS / "forward_tracking"
DEFAULT_SECTOR = HARNESS / f"intraday_sector_screen_{datetime.now().astimezone().strftime('%Y%m%d')}.json"
DEFAULT_JSON = HARNESS / "adaptive_regime_shadow_latest.json"
DEFAULT_MD = HARNESS / "adaptive_regime_shadow_latest.md"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _latest_forward(directory: Path) -> tuple[dict[str, Any], Path | None]:
    paths = sorted(directory.glob("forward_*.json"), reverse=True)
    return (_load(paths[0]), paths[0]) if paths else ({}, None)


def _markdown(plan: dict[str, Any], sources: dict[str, str | None]) -> str:
    lines = [
        "# 적응형 국면 전략 Shadow 계획",
        "",
        f"- 생성시각(UTC): {plan['generated_at_utc']}",
        f"- 상태: **{plan['status']}**",
        f"- 전략: **{plan['strategy']}**",
        "- 실행 단계: **shadow_only (실주문 불가)**",
        f"- 사유: {', '.join(plan.get('reasons') or []) or '없음'}",
        "",
        "## 후보",
    ]
    orders = plan.get("orders") or []
    if not orders:
        lines.append("- 없음")
    else:
        for order in orders:
            lines.append(
                f"- {order['name']}({order['symbol']}): shadow {order['shadow_notional_krw']:,.0f}원, "
                f"수량={order['quantity']}, 근거={order['reason']}"
            )
    lines.extend(["", "## 입력 파일"])
    lines.extend(f"- {name}: {path or '없음'}" for name, path in sources.items())
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", type=Path, default=DEFAULT_LOOP)
    parser.add_argument("--forward-dir", type=Path, default=DEFAULT_FORWARD_DIR)
    parser.add_argument("--sector", type=Path, default=DEFAULT_SECTOR)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_MD)
    parser.add_argument("--max-notional", type=float, default=100_000)
    args = parser.parse_args()

    loop = _load(args.loop)
    forward, forward_path = _latest_forward(args.forward_dir)
    sector = _load(args.sector)
    position_exit = loop.get("position_exit") or {}
    plan = build_shadow_plan(
        now=datetime.now(timezone.utc),
        intraday_decision=((loop.get("intraday") or {}).get("decision") or {}),
        forward_report=forward,
        sector_screen=sector,
        equity_guard=(position_exit.get("equity_guard") or {}),
        performance_gate=(position_exit.get("live_performance_gate") or {}),
        max_notional_krw=args.max_notional,
    )
    sources = {
        "loop": str(args.loop),
        "forward": str(forward_path) if forward_path else None,
        "sector": str(args.sector),
    }
    plan["sources"] = sources
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.out_md.write_text(_markdown(plan, sources), encoding="utf-8")
    print(json.dumps({
        "status": plan["status"],
        "strategy": plan["strategy"],
        "candidate_count": len(plan.get("orders") or []),
        "live_order_submitted": plan["live_order_submitted"],
        "json": str(args.out_json),
        "markdown": str(args.out_md),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
