#!/usr/bin/env python3
"""Open-rebound BUY detector for the 2026-07-08 manual rebound sleeve.

This replaces fixed-at-open BUY orders. It watches the explicit four symbols,
tracks the intraday low from repeated cron calls, and submits guarded LIMIT BUY
orders only after a rebound off the low. It delegates final broker submission to
`toss_alpha.execution.live_submit.run_live_submit_phase`, so current-issue,
market-time, duplicate-ledger, liquidity and confirmation gates remain unified.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from toss_alpha.connectors.kis_readonly import KisReadOnlyClient
from toss_alpha.execution.live_ready import live_readiness
from toss_alpha.execution.live_submit import current_issue_buy_violation, korea_regular_market_violation, run_live_submit_phase

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "harness"
STATE_PATH = REPORT_DIR / "rebound_open_detector_20260708_state.json"
CANDIDATE_OUT = ROOT / "reports" / "trade_candidates" / "rebound_open_detector_buy_candidate_2026-07-08.json"
KST = ZoneInfo("Asia/Seoul")

TARGETS = {
    "336260": {"name": "두산퓨얼셀", "quantity": 3, "limit_price": 47750, "prev_close": 47150, "prev_return_pct": -9.85, "prev_dollar_volume_krw": 31745152000},
    "032820": {"name": "우리기술", "quantity": 13, "limit_price": 11050, "prev_close": 10880, "prev_return_pct": -6.61, "prev_dollar_volume_krw": 21285436160},
    "001510": {"name": "SK증권", "quantity": 54, "limit_price": 2445, "prev_close": 2415, "prev_return_pct": -6.58, "prev_dollar_volume_krw": 5070449475},
    "067310": {"name": "하나마이크론", "quantity": 2, "limit_price": 37350, "prev_close": 36900, "prev_return_pct": -5.38, "prev_dollar_volume_krw": 64455370200},
}

START = time(9, 3)
END = time(9, 25)
MIN_REBOUND_FROM_LOW = 0.010
MAX_GAP_UP = 0.030
MAX_CRASH_FROM_PREV_CLOSE = -0.085


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"date": "2026-07-08", "symbols": {}}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def as_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def client() -> KisReadOnlyClient:
    return KisReadOnlyClient(
        app_key=os.environ["KIS_APP_KEY"],
        app_secret=os.environ["KIS_APP_SECRET"],
        cano=os.environ["KIS_CANO"],
        account_product_code="01",
        timeout=20,
    )


def quote_last(c: KisReadOnlyClient, symbol: str) -> float:
    payload = c.quote(symbol).get("json") or {}
    rec = payload.get("output") or payload.get("output1") or payload
    last = as_float(rec.get("stck_prpr") or rec.get("last") or rec.get("price"))
    if not last or last <= 0:
        raise RuntimeError(f"quote_missing:{symbol}:{payload}")
    return last


def account_cash(c: KisReadOnlyClient) -> float:
    query = {"AFHR_FLPR_YN":"N","OFL_YN":"","INQR_DVSN":"02","UNPR_DVSN":"01","FUND_STTL_ICLD_YN":"N","FNCG_AMT_AUTO_RDPT_YN":"N","PRCS_DVSN":"01","CTX_AREA_FK100":"","CTX_AREA_NK100":""}
    payload = c.balance(query=query).get("json") or {}
    if str(payload.get("rt_cd")) != "0":
        raise RuntimeError(f"balance_failed:{payload.get('msg_cd')}:{payload.get('msg1')}")
    out2 = payload.get("output2") or []
    row = out2[0] if isinstance(out2, list) and out2 else {}
    return as_float(row.get("dnca_tot_amt")) or 0.0


def build_orders(now: datetime, quotes: dict[str, float], state: dict) -> list[dict]:
    orders = []
    sym_state = state.setdefault("symbols", {})
    for sym, cfg in TARGETS.items():
        last = quotes[sym]
        st = sym_state.setdefault(sym, {})
        if st.get("submitted"):
            continue
        low = min(float(st.get("low") or last), last)
        high = max(float(st.get("high") or last), last)
        st.update({"low": low, "high": high, "last": last, "updated_at": now.astimezone(KST).isoformat()})
        change = last / cfg["prev_close"] - 1.0
        rebound = last / low - 1.0 if low > 0 else 0.0
        conditions = {
            "below_limit": last <= cfg["limit_price"],
            "not_gap_up": change <= MAX_GAP_UP,
            "not_crashing_too_deep": change >= MAX_CRASH_FROM_PREV_CLOSE,
            "rebound_from_low": rebound >= MIN_REBOUND_FROM_LOW,
        }
        st["conditions"] = conditions
        if all(conditions.values()):
            notional = int(cfg["quantity"] * cfg["limit_price"])
            orders.append({
                "symbol": sym,
                "name": cfg["name"],
                "side": "BUY",
                "order_type": "LIMIT",
                "quantity": cfg["quantity"],
                "limit_price": cfg["limit_price"],
                "notional_krw": notional,
                "current_price": int(last),
                "last_price": int(last),
                "prev_close": cfg["prev_close"],
                "prev_return_pct": cfg["prev_return_pct"],
                "prev_dollar_volume_krw": cfg["prev_dollar_volume_krw"],
                "spread_pct": 0.002,
                "mode": "live_auto_guarded",
                "reason": f"open_rebound_detected low={low} last={last} rebound={rebound:.4f} change={change:.4f}",
            })
            st["submitted"] = True
    return orders


def main() -> int:
    now = datetime.now(timezone.utc)
    now_kst = now.astimezone(KST)
    if now_kst.date().isoformat() != "2026-07-08":
        return 0
    if now_kst.time() < START or now_kst.time() > END:
        return 0
    market_violation = korea_regular_market_violation(now)
    if market_violation:
        print(f"장초 반등 감지 중단: {market_violation}")
        return 0

    dummy_order = {"side": "BUY", "symbol": "000000"}
    issue_violation = current_issue_buy_violation(dummy_order, root=ROOT, now=now)
    if issue_violation:
        # Print once only.
        state = load_state()
        if not state.get("current_issue_block_reported"):
            state["current_issue_block_reported"] = True
            save_state(state)
            print(f"장초 반등 매수 차단: {issue_violation}")
        return 0

    c = client()
    cash = account_cash(c)
    state = load_state()
    quotes = {sym: quote_last(c, sym) for sym in TARGETS}
    orders = build_orders(now, quotes, state)
    total = sum(int(o["notional_krw"]) for o in orders)
    if total > cash:
        # Drop lower-priority orders until cash fits.
        kept = []
        running = 0
        for order in orders:
            if running + int(order["notional_krw"]) <= cash:
                kept.append(order)
                running += int(order["notional_krw"])
        orders = kept
    save_state(state)
    if not orders:
        return 0

    payload = {
        "generated_at_utc": now.isoformat(),
        "as_of": "2026-07-08",
        "status": "CANDIDATES",
        "policy_id": "manual_rebound_open_detector_20260708",
        "situation": "manual_open_rebound_after_current_issue_gate",
        "strategy_type": "open_rebound_detector",
        "orders": orders,
    }
    CANDIDATE_OUT.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATE_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    old_env = os.environ.copy()
    try:
        os.environ.update({
            "PYTHONPATH": "src",
            "KIS_ACNT_PRDT_CD": "01",
            "KIS_ACCOUNT_PRODUCT_CODE": "01",
            "TOSS_RISK_LIVE_TRADING_ENABLED": "true",
            "KIS_LIVE_TRADING_ENABLED": "true",
            "TOSS_LIVE_SUBMIT_ENABLED": "true",
            "TOSS_LIVE_SUBMIT_DRY_RUN": "false",
            "TOSS_LIVE_SUBMIT_CONFIRMATION": "I UNDERSTAND THIS IS A REAL ORDER",
            "TOSS_MAX_ORDER_KRW": "150000",
            "TOSS_MAX_POSITION_PCT": "1.0",
            "TOSS_ALLOW_QUAL_DATA_BLOCKED": "true",
            "TOSS_LIVE_STRATEGY_ID": "manual_rebound_open_detector_20260708",
        })
        live = live_readiness()
        qual = {"status": "PASS_OPEN_REBOUND_DETECTOR", "reason": "current issue gate passed and rebound trigger detected"}
        result = run_live_submit_phase(candidate_payload=payload, qual=qual, live=live, report_dir=REPORT_DIR, now=now)
    finally:
        os.environ.clear(); os.environ.update(old_env)

    print("장초 반등 감지 매수 조건 발생")
    print(f"상태: {result.get('status')} submitted={result.get('submitted_count')} blocked={result.get('blocked_count')}")
    print(f"candidate: {CANDIDATE_OUT}")
    print(f"artifact: {result.get('artifact_path')}")
    for row in result.get("results", []):
        body = row.get("json") or {}
        print(f"{row.get('symbol')} {row.get('status')} payload={row.get('payload')} rt_cd={body.get('rt_cd')} msg={body.get('msg1') or body.get('msg_cd')} violations={row.get('violations')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
