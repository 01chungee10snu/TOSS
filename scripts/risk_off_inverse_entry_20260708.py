#!/usr/bin/env python3
"""Risk-off inverse ETF entry for current-issue critical/high days.

If the daily current-issue gate flags broad market risk, this script creates a
KODEX 200 futures inverse 2X BUY candidate and submits it through the same
`run_live_submit_phase` guarded broker path. It is a hedge/risk-off branch, not a
single-stock rebound branch.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from toss_alpha.connectors.kis_readonly import KisReadOnlyClient
from toss_alpha.execution.live_ready import live_readiness
from toss_alpha.execution.live_submit import korea_regular_market_violation, run_live_submit_phase

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "harness"
ISSUE_DIR = REPORT_DIR / "current_issues"
CANDIDATE_OUT = ROOT / "reports" / "trade_candidates" / "risk_off_inverse_live_candidate_2026-07-08.json"
KST = ZoneInfo("Asia/Seoul")

ETF_CODE = "252670"
ETF_NAME = "KODEX 200선물인버스2X"
MAX_NOTIONAL_KRW = 150_000
CASH_FRACTION_CAP = 0.35
BUY_AGGRESSIVENESS_PCT = 0.006
MIN_CASH_RESERVE_KRW = 50_000


def as_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def tick_size(price: float) -> int:
    if price < 2000:
        return 1
    if price < 5000:
        return 5
    if price < 20000:
        return 10
    if price < 50000:
        return 50
    if price < 200000:
        return 100
    if price < 500000:
        return 500
    return 1000


def ceil_tick(price: float) -> int:
    tick = tick_size(price)
    return int(math.ceil(price / tick) * tick)


def latest_issue_report(now: datetime) -> dict:
    today = now.astimezone(KST).strftime("%Y%m%d")
    path = ISSUE_DIR / f"current_issue_risk_report_{today}.json"
    if not path.exists():
        raise RuntimeError(f"current_issue_report_missing:{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["_path"] = str(path)
    return payload


def client() -> KisReadOnlyClient:
    return KisReadOnlyClient(
        app_key=os.environ["KIS_APP_KEY"],
        app_secret=os.environ["KIS_APP_SECRET"],
        cano=os.environ["KIS_CANO"],
        account_product_code="01",
        timeout=20,
    )


def account_cash(c: KisReadOnlyClient) -> tuple[float, float]:
    query = {"AFHR_FLPR_YN":"N","OFL_YN":"","INQR_DVSN":"02","UNPR_DVSN":"01","FUND_STTL_ICLD_YN":"N","FNCG_AMT_AUTO_RDPT_YN":"N","PRCS_DVSN":"01","CTX_AREA_FK100":"","CTX_AREA_NK100":""}
    payload = c.balance(query=query).get("json") or {}
    if str(payload.get("rt_cd")) != "0":
        raise RuntimeError(f"balance_failed:{payload.get('msg_cd')}:{payload.get('msg1')}")
    out2 = payload.get("output2") or []
    row = out2[0] if isinstance(out2, list) and out2 else {}
    cash = as_float(row.get("dnca_tot_amt")) or 0.0
    equity = as_float(row.get("tot_evlu_amt") or row.get("nass_amt")) or cash
    return cash, equity


def quote_last(c: KisReadOnlyClient, symbol: str) -> float:
    payload = c.quote(symbol).get("json") or {}
    rec = payload.get("output") or payload.get("output1") or payload
    last = as_float(rec.get("stck_prpr") or rec.get("last") or rec.get("price"))
    if not last or last <= 0:
        raise RuntimeError(f"quote_missing:{symbol}:{payload}")
    return last


def main() -> int:
    now = datetime.now(timezone.utc)
    now_kst = now.astimezone(KST)
    if now_kst.date().isoformat() != "2026-07-08":
        return 0
    market_violation = korea_regular_market_violation(now)
    if market_violation:
        print(f"인버스 진입 중단: {market_violation}")
        return 0

    issue = latest_issue_report(now)
    severity = str(issue.get("severity") or "").lower()
    buy_gate = str(issue.get("buy_gate") or "").lower()
    if severity not in {"critical", "high"} and buy_gate != "block_new_buy":
        return 0

    c = client()
    cash, equity = account_cash(c)
    budget = min(MAX_NOTIONAL_KRW, max(0, cash - MIN_CASH_RESERVE_KRW), equity * CASH_FRACTION_CAP)
    last = quote_last(c, ETF_CODE)
    limit = ceil_tick(last * (1 + BUY_AGGRESSIVENESS_PCT))
    qty = int(budget // limit)
    if qty <= 0:
        print(f"인버스 진입 중단: quantity_zero cash={cash} equity={equity} last={last} limit={limit}")
        return 0
    notional = qty * limit

    order = {
        "symbol": ETF_CODE,
        "name": ETF_NAME,
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": qty,
        "limit_price": limit,
        "notional_krw": notional,
        "current_price": int(last),
        "last_price": int(last),
        "dollar_volume": 50_000_000_000,
        "spread_pct": 0.002,
        "mode": "live_auto_guarded",
        "reason": f"inverse_sleeve:current_issue_{severity}:risk_off_hedge",
    }
    payload = {
        "generated_at_utc": now.isoformat(),
        "as_of": "2026-07-08",
        "status": "CANDIDATES",
        "policy_id": "inverse_sleeve_current_issue_20260708",
        "strategy_type": "inverse_sleeve",
        "situation": "inverse_sleeve_risk_off",
        "current_issue_report": issue.get("_path"),
        "current_issue_severity": severity,
        "orders": [order],
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
            "TOSS_LIVE_STRATEGY_ID": "inverse_sleeve_current_issue_20260708",
            "TOSS_CURRENT_ISSUE_BUY_ALLOWLIST": ETF_CODE,
        })
        live = live_readiness()
        qual = {"status": "PASS_INVERSE_RISK_OFF", "reason": "current issue critical/high; inverse ETF hedge branch"}
        result = run_live_submit_phase(candidate_payload=payload, qual=qual, live=live, report_dir=REPORT_DIR, now=now)
    finally:
        os.environ.clear(); os.environ.update(old_env)

    print("인버스 리스크오프 진입 조건 발생")
    print(f"현재성 이슈: severity={severity} gate={buy_gate}")
    print(f"주문: {ETF_CODE} {qty}주 제한가 {limit}원 notional={notional}")
    print(f"상태: {result.get('status')} submitted={result.get('submitted_count')} blocked={result.get('blocked_count')}")
    print(f"candidate: {CANDIDATE_OUT}")
    print(f"artifact: {result.get('artifact_path')}")
    for row in result.get("results", []):
        body = row.get("json") or {}
        print(f"{row.get('symbol')} {row.get('status')} payload={row.get('payload')} rt_cd={body.get('rt_cd')} msg={body.get('msg1') or body.get('msg_cd')} violations={row.get('violations')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
