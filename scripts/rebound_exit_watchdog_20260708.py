#!/usr/bin/env python3
"""2026-07-08 manual rebound sleeve exit watchdog.

Silent watchdog by default. Prints only when it submits/blocks an exit or hits an error.
It monitors the four explicitly authorized rebound BUY candidates and submits guarded
LIMIT SELL orders for stop/take-profit/trailing/time-exit conditions.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from toss_alpha.connectors.kis_readonly import KisReadOnlyClient
from toss_alpha.execution.live_ready import live_readiness
from toss_alpha.execution.live_submit import korea_regular_market_violation, run_live_submit_phase

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "harness"
STATE_PATH = REPORT_DIR / "rebound_exit_watchdog_20260708_state.json"
CANDIDATE_OUT = ROOT / "reports" / "trade_candidates" / "rebound_exit_sell_candidate_2026-07-08.json"
KST = ZoneInfo("Asia/Seoul")

TARGETS = {
    "336260": {"name": "두산퓨얼셀", "planned_qty": 3, "buy_limit": 47750, "prev_close": 47150},
    "032820": {"name": "우리기술", "planned_qty": 13, "buy_limit": 11050, "prev_close": 10880},
    "001510": {"name": "SK증권", "planned_qty": 54, "buy_limit": 2445, "prev_close": 2415},
    "067310": {"name": "하나마이크론", "planned_qty": 2, "buy_limit": 37350, "prev_close": 36900},
}

STOP_LOSS_PCT = 0.03
TAKE_PROFIT_PCT = 0.05
TAKE_PROFIT_STRONG_PCT = 0.08
TRAILING_DROP_PCT = 0.02
FIRST_WINDOW_END = time(9, 8)
TIME_EXIT = time(10, 30)
LAST_EXIT = time(15, 10)


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"date": "2026-07-08", "symbols": {}}


def save_state(state: dict) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def as_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def tick_size(price: float) -> int:
    if price < 1000:
        return 1
    if price < 5000:
        return 5
    if price < 10000:
        return 10
    if price < 50000:
        return 50
    if price < 100000:
        return 100
    if price < 500000:
        return 500
    return 1000


def floor_tick(price: float) -> int:
    tick = tick_size(price)
    return max(tick, int(math.floor(price / tick) * tick))


def client() -> KisReadOnlyClient:
    return KisReadOnlyClient(
        app_key=os.environ["KIS_APP_KEY"],
        app_secret=os.environ["KIS_APP_SECRET"],
        cano=os.environ["KIS_CANO"],
        account_product_code="01",
        timeout=20,
    )


def balance_payload(c: KisReadOnlyClient) -> dict:
    query = {
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "01",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }
    payload = c.balance(query=query).get("json") or {}
    if str(payload.get("rt_cd")) != "0":
        raise RuntimeError(f"balance_failed:{payload.get('msg_cd')}:{payload.get('msg1')}")
    return payload


def positions_by_symbol(payload: dict) -> dict:
    out = {}
    for row in payload.get("output1") or []:
        sym = str(row.get("pdno") or "").zfill(6)
        if sym in TARGETS:
            qty = as_float(row.get("hldg_qty")) or 0.0
            sellable = as_float(row.get("ord_psbl_qty"))
            if qty > 0:
                out[sym] = {
                    "qty": qty,
                    "sellable": qty if sellable is None else sellable,
                    "avg_price": as_float(row.get("pchs_avg_pric")) or TARGETS[sym]["buy_limit"],
                    "market_value": as_float(row.get("evlu_amt")),
                    "raw": row,
                }
    return out


def quote_last(c: KisReadOnlyClient, symbol: str) -> float:
    payload = c.quote(symbol).get("json") or {}
    rec = payload.get("output") or payload.get("output1") or payload
    last = as_float(rec.get("stck_prpr") or rec.get("last") or rec.get("price"))
    if not last or last <= 0:
        raise RuntimeError(f"quote_missing:{symbol}:{payload}")
    return last


def half_qty(qty: int) -> int:
    if qty <= 1:
        return 1
    return max(1, qty // 2)


def decide_orders(now_kst: datetime, positions: dict, quotes: dict, state: dict) -> list[dict]:
    orders = []
    symbols_state = state.setdefault("symbols", {})
    for sym, pos in positions.items():
        qty = int(pos["qty"])
        sellable = int(pos["sellable"] or 0)
        if qty <= 0 or sellable <= 0:
            continue
        last = float(quotes[sym])
        st = symbols_state.setdefault(sym, {})
        entry = float(st.get("entry_price") or pos.get("avg_price") or TARGETS[sym]["buy_limit"])
        st["entry_price"] = entry
        st["last_seen_qty"] = qty
        st["last_price"] = last
        st["updated_at"] = now_kst.isoformat()
        high = max(float(st.get("high") or entry), last)
        st["high"] = high
        if now_kst.time() <= FIRST_WINDOW_END:
            low = min(float(st.get("first_window_low") or last), last)
            st["first_window_low"] = low
        first_low = float(st.get("first_window_low") or entry)
        stop = floor_tick(entry * (1 - STOP_LOSS_PCT))
        tp = floor_tick(entry * (1 + TAKE_PROFIT_PCT))
        strong_tp = floor_tick(entry * (1 + TAKE_PROFIT_STRONG_PCT))
        trail = floor_tick(high * (1 - TRAILING_DROP_PCT))
        reason = None
        sell_qty = 0
        if last <= stop:
            reason = f"STOP_LOSS_3pct last={last} stop={stop} entry={entry}"
            sell_qty = sellable
        elif now_kst.time() > FIRST_WINDOW_END and last < first_low:
            reason = f"FIRST_5MIN_LOW_BREAK last={last} first_low={first_low}"
            sell_qty = sellable
        elif st.get("trim_done") and last <= trail:
            reason = f"TRAILING_STOP_2pct last={last} high={high} trail={trail}"
            sell_qty = sellable
        elif last >= strong_tp:
            reason = f"STRONG_TAKE_PROFIT_8pct last={last} strong_tp={strong_tp}"
            sell_qty = sellable
        elif (not st.get("trim_done")) and last >= tp:
            reason = f"TAKE_PROFIT_5pct_TRIM last={last} tp={tp}"
            sell_qty = min(sellable, half_qty(qty))
            st["trim_done"] = True
        elif now_kst.time() >= TIME_EXIT and last <= entry:
            reason = f"TIME_EXIT_NO_REBOUND last={last} entry={entry}"
            sell_qty = sellable
        elif now_kst.time() >= LAST_EXIT:
            reason = f"LAST_EXIT_BEFORE_CLOSE last={last} entry={entry}"
            sell_qty = sellable
        if reason and sell_qty > 0:
            limit_price = floor_tick(last * 0.995)
            orders.append({
                "symbol": sym,
                "name": TARGETS[sym]["name"],
                "side": "SELL",
                "order_type": "LIMIT",
                "quantity": int(sell_qty),
                "sellable_quantity": int(sellable),
                "limit_price": int(limit_price),
                "notional_krw": int(limit_price * sell_qty),
                "mode": "live_auto_guarded",
                "reason": reason,
                "entry_price": entry,
                "last_price": last,
                "high_price": high,
                "first_window_low": first_low,
            })
    return orders


def human_status(result: dict) -> str:
    status = result.get("status")
    submitted = int(result.get("submitted_count") or 0)
    blocked = int(result.get("blocked_count") or 0)
    if status == "LIVE_SUBMITTED" and submitted:
        return f"매도 주문 접수 완료 ({submitted}건)"
    if blocked:
        return f"매도 신호는 있었지만 주문 차단 ({blocked}건)"
    return str(status or "상태 미확인")


def human_reason(reason: str) -> str:
    if reason.startswith("STOP_LOSS_3pct"):
        return "손절선 도달"
    if reason.startswith("FIRST_5MIN_LOW_BREAK"):
        return "장초 저점 이탈"
    if reason.startswith("TRAILING_STOP_2pct"):
        return "고점 대비 하락으로 이익보호"
    if reason.startswith("STRONG_TAKE_PROFIT_8pct"):
        return "강한 익절선 도달"
    if reason.startswith("TAKE_PROFIT_5pct_TRIM"):
        return "5% 수익권 일부익절"
    if reason.startswith("TIME_EXIT_NO_REBOUND"):
        return "10:30까지 반등 실패"
    if reason.startswith("LAST_EXIT_BEFORE_CLOSE"):
        return "장마감 전 정리"
    return reason


def human_violation(violation: str) -> str:
    mapping = {
        "liquidity_quality_missing_dollar_volume": "거래대금 정보 부족",
        "liquidity_quality_missing_spread": "호가 스프레드 정보 부족",
        "market_regime_missing": "시장상황 정보 부족",
        "risk_decision_blocked": "리스크 게이트 차단",
        "duplicate_live_order_ledger_key": "중복 주문 방지",
        "sellable_quantity_shortfall": "매도가능수량 부족",
        "sellable_quantity_missing": "매도가능수량 확인 실패",
    }
    return mapping.get(str(violation), str(violation))


def main() -> int:
    now = datetime.now(timezone.utc)
    now_kst = now.astimezone(KST)
    state = load_state()

    if now_kst.date().isoformat() != "2026-07-08":
        return 0
    market_violation = korea_regular_market_violation(now)
    if market_violation:
        # Silent outside regular market except after configured watch day starts.
        if now_kst.time() >= time(9, 0):
            print(f"감시 중단: {market_violation}")
        return 0

    c = client()
    bal = balance_payload(c)
    positions = positions_by_symbol(bal)
    if not positions:
        save_state(state)
        return 0

    quotes = {sym: quote_last(c, sym) for sym in positions}
    orders = decide_orders(now_kst, positions, quotes, state)
    save_state(state)
    if not orders:
        return 0

    payload = {
        "generated_at_utc": now.isoformat(),
        "as_of": "2026-07-08",
        "status": "CANDIDATES",
        "policy_id": "manual_rebound_exit_watchdog_20260708",
        "strategy_type": "manual_rebound_exit_watchdog",
        "orders": orders,
        "state_path": str(STATE_PATH),
    }
    CANDIDATE_OUT.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATE_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    env = dict(os.environ)
    env.update({
        "PYTHONPATH": "src",
        "KIS_ACNT_PRDT_CD": "01",
        "KIS_ACCOUNT_PRODUCT_CODE": "01",
        "TOSS_RISK_LIVE_TRADING_ENABLED": "true",
        "KIS_LIVE_TRADING_ENABLED": "true",
        "TOSS_LIVE_SUBMIT_ENABLED": "true",
        "TOSS_LIVE_SUBMIT_DRY_RUN": "false",
        "TOSS_LIVE_SUBMIT_CONFIRMATION": "I UNDERSTAND THIS IS A REAL ORDER",
        "TOSS_MAX_ORDER_KRW": "1000000",
        "TOSS_MAX_POSITION_PCT": "1.0",
        "TOSS_ALLOW_QUAL_DATA_BLOCKED": "true",
        "TOSS_LIVE_STRATEGY_ID": "manual_rebound_exit_watchdog_20260708",
    })

    # Use in-process call so we keep exact artifact return values.
    old_env = os.environ.copy()
    try:
        os.environ.clear(); os.environ.update(env)
        live = live_readiness()
        qual = {"status": "PASS_MANUAL_EXIT_WATCHDOG", "reason": "authorized rebound sleeve exit watchdog"}
        result = run_live_submit_phase(candidate_payload=payload, qual=qual, live=live, report_dir=REPORT_DIR, now=now)
    finally:
        os.environ.clear(); os.environ.update(old_env)

    print("🚨 손절·익절 감시 알림")
    print(f"- 시각: {now_kst.strftime('%H:%M:%S')}")
    print(f"- 결과: {human_status(result)}")
    for order, row in zip(orders, result.get("results", [])):
        body = row.get("json") or {}
        reason = human_reason(str(order.get("reason") or ""))
        msg = body.get("msg1") or body.get("msg_cd") or ""
        violations = row.get("violations") or []
        print(f"- 종목: {order.get('name')} `{order.get('symbol')}`")
        print(f"- 신호: {reason}")
        print(f"- 주문: {order.get('quantity')}주 SELL, 제한가 {int(order.get('limit_price') or 0):,}원")
        if row.get("status") == "SUBMITTED":
            output = body.get("output") or {}
            print(f"- 접수: 성공, 주문번호 `{output.get('ODNO')}`")
        elif msg:
            print(f"- 브로커 메시지: {msg}")
        if violations:
            print("- 차단 사유: " + ", ".join(human_violation(v) for v in violations))
    print(f"- 상세파일: {result.get('artifact_path')}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"손절익절 감시 오류: {exc!r}")
        raise
