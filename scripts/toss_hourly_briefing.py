#!/usr/bin/env python3
"""Read-only, deterministic TOSS/KIS hourly operator briefing."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.toss_intraday_realtime_guard import collect_snapshot
from scripts.toss_market_close_settlement import fifo_realized, ledger_orders_with_reconcile

ROOT = Path(__file__).resolve().parents[1]
LOOP_REPORT = ROOT / "reports" / "harness" / "latest_loop_report.json"
KST = ZoneInfo("Asia/Seoul")


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    now = datetime.now(timezone.utc)
    today = now.astimezone(KST).date().isoformat()
    snapshot = collect_snapshot(now)
    orders = ledger_orders_with_reconcile()
    realized, stats = fifo_realized(orders, realized_date=today)
    today_orders = [o for o in orders if str(o.get("order_date") or "") == today]
    positions = snapshot.get("positions") or []
    unrealized = sum(float(p.get("unrealized_pnl") or 0) for p in positions)
    realized_total = sum(float(v or 0) for v in realized.values())
    gross_total = realized_total + unrealized
    account = snapshot.get("account") or {}
    issue = snapshot.get("current_issue") or {}
    loop = load_json(LOOP_REPORT)
    decision = ((loop.get("intraday") or {}).get("decision") or {})

    print("## TOSS 시간별 상황 브리핑")
    print(f"- 기준: {now.astimezone(KST).strftime('%Y-%m-%d %H:%M:%S KST')}")
    print(f"- 계좌: 총자산 {float(account.get('total_equity') or 0):,.0f}원 / 현금 {float(account.get('cash') or 0):,.0f}원")
    print(f"- 오늘 손익: 확정 {realized_total:+,.0f}원 / 평가 {unrealized:+,.0f}원 / 비용 전 합계 {gross_total:+,.0f}원")
    print("- 비용: 당일 수수료·제비용은 장 마감 KIS 정산에서 최종 반영합니다.")
    print(f"- 위험 게이트: {issue.get('severity', 'unknown')} / {issue.get('buy_gate', 'unknown')} / score={issue.get('risk_score')}")
    print(f"- 실행 판단: {decision.get('verdict') or loop.get('overall_status') or '확인 불가'} ({decision.get('reason') or '사유 없음'})")
    if positions:
        for p in positions[:5]:
            cost = float(p.get("avg_price") or 0) * float(p.get("qty") or 0)
            pnl = float(p.get("unrealized_pnl") or 0)
            pct = pnl / cost * 100 if cost else 0.0
            print(f"- 보유: {p.get('name') or p.get('symbol')} {float(p.get('qty') or 0):g}주, 평단 {float(p.get('avg_price') or 0):,.0f}원, 현재 {float(p.get('last') or 0):,.0f}원, {pnl:+,.0f}원({pct:+.2f}%)")
    else:
        print("- 보유: 없음")
    filled = [o for o in today_orders if str(o.get("status")) in {"FILLED", "PARTIALLY_FILLED", "PARTIALLY_FILLED_CANCELED"}]
    print(f"- 오늘 체결: {len(filled)}건")
    for o in filled[-6:]:
        print(f"  - {o.get('name') or o.get('symbol')} {o.get('side')} {float(o.get('qty') or 0):g}주 @ {float(o.get('avg') or 0):,.0f}원")
    unmatched = sum(float((row or {}).get("unmatched_sell_qty") or 0) for row in stats.values())
    if unmatched:
        print(f"- 주의: 원가 미확인 매도 {unmatched:g}주가 있어 확정손익 일부가 미확정입니다.")
    print("- 이 브리핑은 읽기 전용이며 주문을 제출하지 않습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())