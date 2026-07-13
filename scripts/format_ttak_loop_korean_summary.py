#!/usr/bin/env python3
"""Format TOSS ttak-loop JSON report as a concise Korean Telegram summary."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _get(d: dict[str, Any], path: str, default: Any = None) -> Any:
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(part)
    return default if cur is None else cur


def _kr_status(status: str | None) -> str:
    mapping = {
        "ACTIONABLE_CANDIDATES": "후보 있음",
        "CANDIDATES": "후보 있음",
        "NO_TRADE": "거래 없음",
        "READY": "통과",
        "LIVE_READY": "실주문 준비됨",
        "LIVE_SUBMIT_BLOCKED": "실주문 차단",
        "LIVE_SUBMIT_NO_ORDERS": "주문 없음",
        "LIVE_SUBMIT_DRY_RUN_READY": "모의주문 가능",
        "LIVE_SUBMIT_SUBMITTED": "실주문 제출",
        "BLOCK": "차단",
    }
    return mapping.get(str(status), str(status or "확인불가"))


def _kr_violation(v: str) -> str:
    mapping = {
        "candidate_as_of_not_recent_krx_trading_day": "후보 기준일이 최신 거래일이 아님",
        "panel_latest_date_stale": "패널 데이터가 최신이 아님",
        "risk_decision_blocked": "리스크 판단으로 차단",
        "duplicate_live_order_ledger_key": "같은 날 같은 종목/방향 주문 중복",
        "intraday_submit_cap_exceeded": "일중 주문 한도 초과",
        "live_readiness_not_ready": "실주문 준비상태 미충족",
        "qual_gate_blocked": "정성/뉴스 게이트 차단",
        "outside_krx_trading_day": "KRX 거래일 아님",
        "before_korea_regular_market_open_0900_kst": "09:00 장 시작 전",
        "after_korea_regular_market_last_buy_1520_kst": "15:20 주문 허용 마감 후",
    }
    if v.startswith("market_regime_blocked:"):
        return f"시장 국면 차단({v.split(':', 1)[1]})"
    return mapping.get(v, v)


def _fmt_money(value: Any) -> str:
    try:
        return f"{float(value):,.0f}원"
    except Exception:
        return "-"


def main() -> int:
    if len(sys.argv) < 2:
        print("사용법: format_ttak_loop_korean_summary.py REPORT_JSON", file=sys.stderr)
        return 2
    path = Path(sys.argv[1]).expanduser()
    data = json.loads(path.read_text(encoding="utf-8"))

    overall = _get(data, "overall_status")
    live_submit = _get(data, "live_submit", {}) or {}
    quant = _get(data, "quant", {}) or {}
    payload = _get(data, "execution_candidate_payload", {}) or _get(quant, "candidate_payload", {}) or {}
    pos = _get(data, "position_exit", {}) or {}
    order_reconcile = _get(live_submit, "order_reconcile", {}) or {}
    equity = _get(pos, "equity_guard", {}) or {}
    live = _get(data, "live", {}) or {}
    qual = _get(data, "qual", {}) or {}

    orders = list(payload.get("orders") or [])
    first_order = orders[0] if orders else {}
    submitted = int(live_submit.get("submitted_count") or 0)
    blocked = int(live_submit.get("blocked_count") or 0)
    order_count = int(live_submit.get("order_count") or len(orders) or 0)
    violations = list(live_submit.get("violations") or [])

    if submitted > 0:
        conclusion = f"✅ 실주문 {submitted}건 제출됐습니다."
    elif order_count > 0 and blocked > 0:
        conclusion = "⛔ 후보는 있었지만 실주문은 차단됐습니다."
    elif order_count == 0:
        conclusion = "✅ 이번 실행에서는 주문 후보가 없어서 거래하지 않았습니다."
    else:
        conclusion = f"ℹ️ 실행 상태: {_kr_status(str(overall))}"

    print(conclusion)
    print("")
    print("## 판단 결과")
    print(f"- 전체 상태: {_kr_status(str(overall))}")
    print(f"- 실주문 상태: {_kr_status(str(live_submit.get('status')))}")
    print(f"- 실주문 준비: {_kr_status(str(live.get('status')))}")
    print(f"- 정성/뉴스 게이트: {_kr_status(str(qual.get('status')))}")
    print(f"- 매도 후보: {int(pos.get('sell_order_count') or 0)}건")
    print(f"- 신규/헤지 후보: {order_count}건")
    print("")

    if first_order:
        print("## 후보 주문")
        print(f"- 종목: {first_order.get('name') or '-'} ({first_order.get('symbol') or '-'})")
        print(f"- 방향: {first_order.get('side') or '-'}")
        print(f"- 수량/가격: {first_order.get('quantity') or '-'}주 × {first_order.get('limit_price') or '-'}원")
        print(f"- 주문금액: {_fmt_money(first_order.get('notional_krw'))}")
        print(f"- 전략: {payload.get('strategy_type') or quant.get('strategy_type') or '-'}")
        print(f"- 이유: {first_order.get('reason') or '-'}")
        print("")

    print("## 차단 사유" if violations else "## 차단 사유")
    if violations:
        for v in violations:
            print(f"- {_kr_violation(str(v))}")
    else:
        print("- 없음")
    print("")

    print("## 보유/계좌 안전장치")
    if order_count == 0 and not equity.get("current_equity"):
        print("- 계좌 가드: 생략 (주문 후보 없음)")
        print("- 현재 평가자산: -")
        print("- 신규매수 차단 여부: 해당 없음")
    else:
        print(f"- 계좌 가드: {_kr_status(str(equity.get('status')))}")
        print(f"- 현재 평가자산: {_fmt_money(equity.get('current_equity'))}")
        print(f"- 고점 대비 하락률: {float(equity.get('drawdown_pct') or 0.0):.2%}")
        print(f"- 신규매수 차단 여부: {bool(equity.get('block_new_buys'))}")
    print("")

    print("## 체결/미체결 관리")
    rec_status = str(order_reconcile.get("status") or "-")
    print(f"- 상태: {rec_status}")
    if order_reconcile:
        print(f"- 조회한 active 주문: {int(order_reconcile.get('checked_count') or 0)}건")
        print(f"- ledger 갱신: {int(order_reconcile.get('updated_count') or 0)}건")
        print(f"- 취소 시도: {int(order_reconcile.get('cancel_attempted_count') or 0)}건")
        print(f"- 자동취소 활성화: {bool(order_reconcile.get('cancel_enabled'))}")
    print("")

    print("## 파일")
    print(f"- 리포트: {path}")
    artifact = live_submit.get("artifact_path")
    if artifact:
        print(f"- 주문검증: {artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
