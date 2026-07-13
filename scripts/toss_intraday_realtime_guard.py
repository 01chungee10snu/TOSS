#!/usr/bin/env python3
"""Intraday real-time guard for TOSS/KIS live operation.

Modes:
- monitor: 5-minute market/position situation check. Saves state and prints only
  on meaningful changes or 30-minute heartbeat.
- decision: 15-minute conservative decision brief. Does not place new BUY orders;
  SELL is handled by the 1-minute watchdog. This keeps real-time awareness without
  noisy overtrading.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from toss_alpha.connectors.kis_readonly import KisReadOnlyClient
from toss_alpha.execution.live_ready import LiveExecutionConfig

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "harness"
STATE_PATH = REPORT_DIR / "intraday_realtime_guard_state.json"
OUT_PATH = REPORT_DIR / "intraday_realtime_guard_latest.json"
KST = ZoneInfo("Asia/Seoul")
WATCH_SYMBOLS = {
    "001510": "SK증권",
    "032820": "우리기술",
    "307930": "컴퍼니케이",
    "252670": "KODEX 200선물인버스2X",
    "114800": "KODEX 인버스",
}


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def latest_issue(now: datetime) -> dict:
    path = REPORT_DIR / "current_issues" / f"current_issue_risk_report_{now.astimezone(KST).strftime('%Y%m%d')}.json"
    payload = load_json(path, {})
    if payload:
        payload["_path"] = str(path)
    return payload


def kis_client() -> KisReadOnlyClient:
    cfg = LiveExecutionConfig.from_env()
    if not (cfg.app_key and cfg.app_secret and cfg.cano and cfg.account_product_code):
        raise RuntimeError("KIS 설정이 부족합니다")
    return KisReadOnlyClient(
        app_key=cfg.app_key,
        app_secret=cfg.app_secret,
        cano=cfg.cano,
        account_product_code=cfg.account_product_code,
        mock_trading=cfg.kis_mock_trading,
        timeout=cfg.timeout,
    )


def collect_snapshot(now: datetime) -> dict:
    issue = latest_issue(now)
    client = kis_client()
    positions = []
    held_symbols = set()
    for p in client.position_snapshots():
        qty = float(p.quantity or 0)
        if qty <= 0:
            continue
        if p.symbol in WATCH_SYMBOLS or qty > 0:
            held_symbols.add(p.symbol)
            last = None
            try:
                last = client.quote_snapshot(p.symbol).last
            except Exception:
                pass
            cost = (p.avg_price or 0) * (p.quantity or 0)
            pnl = p.unrealized_pnl or 0
            pnl_pct = (pnl / cost * 100) if cost else None
            positions.append({
                "symbol": p.symbol,
                "name": WATCH_SYMBOLS.get(p.symbol, p.symbol),
                "qty": p.quantity,
                "sellable": p.sellable_quantity,
                "avg_price": p.avg_price,
                "last": last,
                "market_value": p.market_value,
                "unrealized_pnl": p.unrealized_pnl,
                "pnl_pct": pnl_pct,
            })
    account = client.account_snapshot()
    snapshot = {
        "generated_at_kst": now.astimezone(KST).isoformat(),
        "mode": None,
        "current_issue": {
            "severity": issue.get("severity", "unknown"),
            "buy_gate": issue.get("buy_gate", "unknown"),
            "risk_score": issue.get("risk_score"),
            "category_counts": issue.get("category_counts") or {},
            "path": issue.get("_path"),
        },
        "account": {
            "cash": account.cash,
            "buying_power": account.buying_power,
            "total_equity": account.total_equity,
        },
        "positions": positions,
        "held_symbols": sorted(held_symbols),
    }
    return snapshot


def position_summary(positions: list[dict]) -> tuple[float, float, list[str]]:
    total_mv = sum(float(p.get("market_value") or 0) for p in positions)
    total_pnl = sum(float(p.get("unrealized_pnl") or 0) for p in positions)
    lines = []
    for p in positions:
        pnl = float(p.get("unrealized_pnl") or 0)
        pct = p.get("pnl_pct")
        pct_text = "-" if pct is None else f"{pct:+.2f}%"
        lines.append(f"{p.get('name')} {int(p.get('qty') or 0)}주 {pnl:+,.0f}원({pct_text})")
    return total_mv, total_pnl, lines


def should_print_monitor(snapshot: dict, state: dict, now: datetime) -> tuple[bool, str]:
    issue = snapshot["current_issue"]
    prev_issue = state.get("last_issue", {})
    if issue.get("severity") != prev_issue.get("severity") or issue.get("buy_gate") != prev_issue.get("buy_gate"):
        return True, "시장 위험도 변화"
    positions = snapshot.get("positions") or []
    alert_bands = {}
    for p in positions:
        pnl_pct = p.get("pnl_pct")
        if pnl_pct is None:
            continue
        band = None
        if pnl_pct <= -4.0:
            band = "loss_4pct"
        elif pnl_pct <= -2.5:
            band = "loss_2_5pct"
        elif pnl_pct >= 6.0:
            band = "profit_6pct"
        elif pnl_pct >= 4.0:
            band = "profit_4pct"
        if band:
            alert_bands[str(p.get("symbol"))] = band
    if alert_bands != (state.get("last_alert_bands") or {}):
        state["last_alert_bands"] = alert_bands
        return True, "보유종목 손익 임계값 접근"
    last_print = state.get("last_monitor_print_kst")
    if not last_print:
        return True, "감시 시작"
    try:
        last_dt = datetime.fromisoformat(last_print)
        if now.astimezone(KST) - last_dt >= timedelta(minutes=30):
            return True, "30분 정기 점검"
    except Exception:
        return True, "상태파일 복구"
    return False, "변화 없음"


def print_monitor(snapshot: dict, reason: str) -> None:
    issue = snapshot["current_issue"]
    total_mv, total_pnl, lines = position_summary(snapshot.get("positions") or [])
    print("📡 시장·보유 감시")
    print(f"- 사유: {reason}")
    print(f"- 시장위험: {issue.get('severity')} / {issue.get('buy_gate')} / score={issue.get('risk_score')}")
    print(f"- 보유평가: {total_mv:,.0f}원, 평가손익 {total_pnl:+,.0f}원")
    for line in lines[:5]:
        print(f"- {line}")
    print("- 원칙: SELL은 빠르게, 신규 BUY는 15분 재판단 후 강한 조건에서만 허용합니다.")


def print_decision(snapshot: dict) -> None:
    issue = snapshot["current_issue"]
    severity = str(issue.get("severity") or "unknown")
    gate = str(issue.get("buy_gate") or "unknown")
    total_mv, total_pnl, lines = position_summary(snapshot.get("positions") or [])
    if gate == "block_new_buy" or severity in {"critical", "high"}:
        buy = "신규 일반 BUY 금지"
        inverse = "인버스는 계좌 ETP 자격 문제로 자동진입 보류"
    elif gate == "allow_with_caution" or severity == "medium":
        buy = "신규 BUY는 소액·제한가·하루 1회 수준만 후보 허용"
        inverse = "인버스 신규진입 보류"
    else:
        buy = "신규 BUY 후보 검토 가능하나 추격매수 금지"
        inverse = "인버스 대기"
    print("🧭 15분 재판단")
    print(f"- 시장위험: {severity} / {gate} / score={issue.get('risk_score')}")
    print(f"- 판단: {buy}")
    print(f"- 방어: {inverse}")
    print(f"- 보유손익: {total_pnl:+,.0f}원 / 평가액 {total_mv:,.0f}원")
    for line in lines[:5]:
        print(f"- {line}")
    print("- 실행: 손절·익절 SELL은 1분 watchdog에 맡기고, 신규 BUY는 과매매 방지로 자동추격하지 않습니다.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["monitor", "decision"], required=True)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    kst = now.astimezone(KST)
    if kst.weekday() >= 5 or kst.time() < datetime.strptime("09:30", "%H:%M").time() or kst.time() > datetime.strptime("14:30", "%H:%M").time():
        return 0
    state = load_json(STATE_PATH, {})
    snapshot = collect_snapshot(now)
    snapshot["mode"] = args.mode
    write_json(OUT_PATH, snapshot)
    if args.mode == "monitor":
        should_print, reason = should_print_monitor(snapshot, state, now)
        if should_print:
            print_monitor(snapshot, reason)
            state["last_monitor_print_kst"] = kst.isoformat()
    else:
        print_decision(snapshot)
        state["last_decision_print_kst"] = kst.isoformat()
    state["last_issue"] = snapshot.get("current_issue") or {}
    state["last_snapshot_path"] = str(OUT_PATH)
    state["updated_at_kst"] = kst.isoformat()
    write_json(STATE_PATH, state)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"실시간 감시 오류: {exc!r}")
        raise
