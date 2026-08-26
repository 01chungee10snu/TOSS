#!/usr/bin/env python3
"""Historical risk-off inverse ETF entry from 2026-07-08.

Signal sizing helpers are retained for offline audit and research. The script
entrypoint is permanently quarantined and cannot call broker APIs or submit an
order.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from toss_alpha.connectors.kis_readonly import KisReadOnlyClient

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
    """Historical 2026-07-08 inverse entry; permanently live-quarantined."""
    print(
        "LEGACY_LIVE_QUARANTINED: risk_off_inverse_entry_20260708 is retained "
        "for offline research only; no broker API or order submission is executed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
