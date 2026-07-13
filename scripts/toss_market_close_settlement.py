#!/usr/bin/env python3
"""Telegram-ready TOSS/KIS market-close settlement.

Read-only. No live orders. Summarizes today's broker order ledger, reconciles fills,
open positions, and account equity into one end-of-day message.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from toss_alpha.connectors.kis_readonly import KisReadOnlyClient
from toss_alpha.execution.live_ready import LiveExecutionConfig
from toss_alpha.execution.order_management import KisOrderStatusClient, parse_status_payload
from toss_alpha.storage.google_sheets import GoogleSheetsClient

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "reports" / "harness" / "live_order_ledger.jsonl"
SETTLEMENT_DIR = ROOT / "reports" / "harness" / "settlements"
DEFAULT_SHEET_ID = "1rIawUGSPb0140dgBEom6BfIcG8MaxgYeuOuQBDcI4Ns"
KST = ZoneInfo("Asia/Seoul")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def order_ids(row: dict) -> tuple[str | None, str | None]:
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    body = result.get("json") if isinstance(result.get("json"), dict) else {}
    output = body.get("output") if isinstance(body.get("output"), dict) else {}
    return output.get("ODNO"), output.get("KRX_FWDG_ORD_ORGNO")


def side_symbol(row: dict) -> tuple[str, str]:
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    side = str(result.get("side") or "").upper()
    symbol = str(result.get("symbol") or "").zfill(6)
    return side, symbol


def _date_from_raw(raw: dict) -> str:
    value = str(raw.get("ord_dt") or "")
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return ""


def _order_from_status(order_no: str, status: dict) -> dict:
    raw = status.get("raw_record") or {}
    return {
        "order_no": order_no,
        "symbol": str(raw.get("pdno") or "").zfill(6),
        "name": raw.get("prdt_name") or str(raw.get("pdno") or "").zfill(6),
        "side": raw.get("sll_buy_dvsn_cd_name") or "",
        "status": status.get("status"),
        "qty": float(status.get("filled_qty") or 0),
        "order_qty": float(status.get("order_qty") or 0),
        "remaining_qty": float(status.get("remaining_qty") or 0),
        "avg": float(raw.get("avg_prvs") or 0),
        "amount": float(raw.get("tot_ccld_amt") or 0),
        "order_date": _date_from_raw(raw),
    }


def _order_identity(order_no: str, order_date: str) -> str:
    """KIS order numbers can repeat on different trading dates."""
    return f"{order_date or 'unknown-date'}:{order_no}"


def ledger_orders_with_reconcile() -> list[dict]:
    today = datetime.now(KST).date().isoformat()
    cfg = LiveExecutionConfig.from_env()
    client = KisOrderStatusClient(cfg)
    by_no: dict[str, dict] = {}
    submitted_today: list[tuple[str, str | None, str, str]] = []
    for row in read_jsonl(LEDGER):
        if isinstance(row.get("broker_status"), dict):
            no = str(row.get("order_no") or "")
            if no:
                order = _order_from_status(no, row["broker_status"])
                by_no[_order_identity(no, str(order.get("order_date") or ""))] = order
            continue
        if row.get("status") == "SUBMITTED":
            no, org = order_ids(row)
            side, symbol = side_symbol(row)
            if no and str(row.get("timestamp", "")).startswith(today):
                submitted_today.append((no, org, side, symbol))
    for no, org, side, symbol in submitted_today:
        try:
            status = parse_status_payload(
                client.inquire_daily_fills(order_no=no, order_orgno=org or "03420", day=datetime.now(KST)),
                order_no=no,
            )
            o = _order_from_status(no, status)
            if str(o.get("symbol") or "") in {"", "000000"}:
                o["symbol"] = symbol
                o["name"] = symbol
            if not o.get("side"):
                o["side"] = side
            if not o.get("order_date"):
                o["order_date"] = today
            by_no[_order_identity(no, str(o.get("order_date") or today))] = o
        except Exception as exc:
            key = _order_identity(no, today)
            by_no.setdefault(key, {"order_no": no, "symbol": symbol, "side": side, "status": "ERROR", "error": repr(exc), "qty": 0, "avg": 0, "amount": 0, "order_date": today})
    return sorted(by_no.values(), key=lambda o: (str(o.get("order_date") or ""), str(o.get("order_no") or "")))


def fifo_period(orders: list[dict], *, period_start: str, period_end: str) -> tuple[dict[str, float], dict[str, dict]]:
    """Match FIFO across all history while reporting only the requested period."""
    lots: dict[str, deque] = defaultdict(deque)
    realized: dict[str, float] = defaultdict(float)
    stats: dict[str, dict] = defaultdict(lambda: {
        "buy_qty": 0.0,
        "buy_amt": 0.0,
        "sell_qty": 0.0,
        "sell_amt": 0.0,
        "unmatched_sell_qty": 0.0,
    })
    for o in orders:
        if str(o.get("status")) not in {"FILLED", "PARTIALLY_FILLED", "PARTIALLY_FILLED_CANCELED"}:
            continue
        qty = float(o.get("qty") or 0)
        if qty <= 0:
            continue
        order_date = str(o.get("order_date") or "")
        in_period = period_start <= order_date <= period_end
        sym = str(o.get("symbol") or "").zfill(6)
        avg = float(o.get("avg") or 0)
        amt = float(o.get("amount") or avg * qty)
        is_sell = "매도" in str(o.get("side")) or str(o.get("side")).upper() == "SELL"
        if not is_sell:
            lots[sym].append([qty, avg])
            if in_period:
                stats[sym]["buy_qty"] += qty
                stats[sym]["buy_amt"] += amt
            continue
        if in_period:
            stats[sym]["sell_qty"] += qty
            stats[sym]["sell_amt"] += amt
        remain = qty
        trade_pnl = 0.0
        while remain > 1e-9 and lots[sym]:
            lot_qty, lot_px = lots[sym][0]
            take = min(remain, lot_qty)
            trade_pnl += take * (avg - lot_px)
            lot_qty -= take
            remain -= take
            if lot_qty <= 1e-9:
                lots[sym].popleft()
            else:
                lots[sym][0][0] = lot_qty
        if in_period:
            realized[sym] += trade_pnl
            if remain > 1e-9:
                stats[sym]["unmatched_sell_qty"] += remain
    return dict(realized), dict(stats)


def fifo_realized(orders: list[dict], *, realized_date: str) -> tuple[dict[str, float], dict[str, dict]]:
    return fifo_period(orders, period_start=realized_date, period_end=realized_date)


def _records(rows: list[list[object]]) -> list[dict[str, str]]:
    if not rows:
        return []
    header = [str(cell).strip() for cell in rows[0]]
    return [
        {header[idx]: str(row[idx]).strip() if idx < len(row) else "" for idx in range(len(header))}
        for row in rows[1:]
        if any(str(cell).strip() for cell in row)
    ]


def read_sheet_snapshot(client: GoogleSheetsClient, spreadsheet_id: str) -> dict[str, list[list[object]]]:
    return {
        "summary": client.get_values(spreadsheet_id, "summary!A:B"),
        "artifacts": client.get_values(spreadsheet_id, "artifacts!A:C"),
        "history": client.get_values(spreadsheet_id, "history!A:L"),
    }


def summarize_sheet_snapshot(snapshot: dict[str, list[list[object]]], *, now: datetime) -> dict:
    summary_records = _records(snapshot.get("summary") or [])
    latest = {row.get("field", ""): row.get("value", "") for row in summary_records if row.get("field")}
    artifacts = _records(snapshot.get("artifacts") or [])
    history = _records(snapshot.get("history") or [])
    week_start = (now.date() - timedelta(days=now.weekday())).isoformat()
    week_end = now.date().isoformat()
    week_history = []
    for row in history:
        try:
            stamp = datetime.fromisoformat(str(row.get("generated_at_utc") or "").replace("Z", "+00:00")).astimezone(KST)
        except ValueError:
            continue
        if week_start <= stamp.date().isoformat() <= week_end:
            week_history.append(row)
    generated = None
    try:
        generated = datetime.fromisoformat(str(latest.get("generated_at_utc") or "").replace("Z", "+00:00"))
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=KST)
    except ValueError:
        pass
    age_seconds = (now - generated.astimezone(now.tzinfo)).total_seconds() if generated and now.tzinfo else None
    return {
        "tabs_read": sum(1 for name in ("summary", "artifacts", "history") if name in snapshot),
        "latest": latest,
        "artifact_count": len(artifacts),
        "history_count": len(history),
        "week_history_count": len(week_history),
        "week_history": week_history,
        "generated_at_utc": latest.get("generated_at_utc"),
        "age_seconds": age_seconds,
        "fresh": age_seconds is not None and -60 <= age_seconds <= 36 * 3600,
    }


def load_settlement_snapshots(period_start: str, period_end: str) -> list[dict]:
    rows = []
    if not SETTLEMENT_DIR.exists():
        return rows
    for path in sorted(SETTLEMENT_DIR.glob("market_close_settlement_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        stamp = str(payload.get("date") or "")
        if period_start <= stamp <= period_end:
            rows.append(payload)
    return rows


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def main() -> int:
    now = datetime.now(KST)
    cfg = LiveExecutionConfig.from_env()
    ro = KisReadOnlyClient(
        app_key=cfg.app_key or "",
        app_secret=cfg.app_secret or "",
        cano=cfg.cano or "",
        account_product_code=cfg.account_product_code or "01",
        mock_trading=cfg.kis_mock_trading,
        timeout=cfg.timeout,
    )
    account = ro.account_snapshot()
    positions = []
    for p in ro.position_snapshots():
        if (p.quantity or 0) > 0:
            cost = float(p.avg_price or 0) * float(p.quantity or 0)
            pct = (float(p.unrealized_pnl or 0) / cost * 100) if cost else 0.0
            positions.append({
                "symbol": p.symbol,
                "qty": p.quantity,
                "avg": p.avg_price,
                "mv": p.market_value,
                "unrealized": p.unrealized_pnl,
                "pnl_pct": pct,
            })
    today = now.date().isoformat()
    orders = ledger_orders_with_reconcile()
    today_orders = [o for o in orders if str(o.get("order_date")) == today]
    realized, stats = fifo_realized(orders, realized_date=today)
    realized_total = sum(realized.values())
    unmatched_sell_qty = sum(float(item.get("unmatched_sell_qty") or 0) for item in stats.values())
    unrealized_total = sum(float(p.get("unrealized") or 0) for p in positions)
    total_pnl = realized_total + unrealized_total

    sheet_id = os.environ.get("TOSS_SETTLEMENT_SHEET_ID", DEFAULT_SHEET_ID).strip()
    sheet_summary: dict = {"status": "UNAVAILABLE", "error": "not_loaded", "tabs_read": 0, "latest": {}}
    try:
        sheet_snapshot = read_sheet_snapshot(GoogleSheetsClient(), sheet_id)
        sheet_summary = {"status": "READY", **summarize_sheet_snapshot(sheet_snapshot, now=now)}
    except Exception as exc:
        sheet_summary = {"status": "UNAVAILABLE", "error": f"{type(exc).__name__}:{exc}", "tabs_read": 0, "latest": {}}

    week_start = (now.date() - timedelta(days=now.weekday())).isoformat()
    week_end = today
    week_realized, week_stats = fifo_period(orders, period_start=week_start, period_end=week_end)
    week_orders = [
        o for o in orders
        if week_start <= str(o.get("order_date") or "") <= week_end
        and str(o.get("status")) in {"FILLED", "PARTIALLY_FILLED", "PARTIALLY_FILLED_CANCELED"}
        and float(o.get("qty") or 0) > 0
    ]
    week_realized_total = sum(week_realized.values())
    week_unmatched_sell_qty = sum(float(item.get("unmatched_sell_qty") or 0) for item in week_stats.values())
    week_buy_amount = sum(float(item.get("buy_amt") or 0) for item in week_stats.values())
    week_sell_amount = sum(float(item.get("sell_amt") or 0) for item in week_stats.values())

    SETTLEMENT_DIR.mkdir(parents=True, exist_ok=True)
    artifact = {
        "generated_at_kst": now.isoformat(),
        "date": today,
        "account": {"total_equity": float(account.total_equity or 0), "cash": float(account.cash or 0)},
        "daily": {
            "realized_matched_fifo": realized_total,
            "unrealized_snapshot": unrealized_total,
            "unmatched_sell_qty": unmatched_sell_qty,
            "fill_count": len([o for o in today_orders if float(o.get("qty") or 0) > 0]),
        },
        "sheet": sheet_summary,
        "weekly": {
            "period_start": week_start,
            "period_end": week_end,
            "realized_matched_fifo": week_realized_total,
            "unmatched_sell_qty": week_unmatched_sell_qty,
            "fill_count": len(week_orders),
            "buy_amount": week_buy_amount,
            "sell_amount": week_sell_amount,
        },
    }
    artifact_path = SETTLEMENT_DIR / f"market_close_settlement_{now.strftime('%Y%m%d')}.json"
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    week_snapshots = load_settlement_snapshots(week_start, week_end)
    sheet_latest = sheet_summary.get("latest") or {}

    lines = [
        f"📊 TOSS 장마감 결산 — {now.strftime('%Y-%m-%d')}",
        "",
        f"- 평가자산: {float(account.total_equity or 0):,.0f}원",
        f"- 현금: {float(account.cash or 0):,.0f}원",
        f"- 실현손익(자동원장 매칭분 FIFO 추정): {realized_total:+,.0f}원",
        f"- 보유 누적 미실현손익: {unrealized_total:+,.0f}원",
        f"- 손익 스냅샷 합계(일간손익 아님): {total_pnl:+,.0f}원",
        "",
        "## 체결 요약",
    ]
    if not today_orders:
        lines.append("- 오늘 확인된 자동주문 체결 없음")
    else:
        for o in today_orders:
            if float(o.get("qty") or 0) <= 0 and o.get("status") not in {"SUBMITTED", "ERROR"}:
                continue
            lines.append(f"- {o.get('name')} `{o.get('symbol')}` {o.get('side')} {o.get('qty'):g}/{o.get('order_qty'):g}주 @ {o.get('avg'):,.0f}원 · {o.get('status')}")
    lines.append("")
    lines.append("## 보유")
    if not positions:
        lines.append("- 보유 없음")
    else:
        for p in positions:
            lines.append(f"- `{p['symbol']}` {p['qty']:g}주, 평가 {p['mv']:,.0f}원, 손익 {p['unrealized']:+,.0f}원 ({p['pnl_pct']:+.2f}%)")
    lines.append("")
    lines.append("## Google Sheets 전략 종합")
    if sheet_summary.get("status") != "READY":
        lines.append(f"- ⚠️ Sheets 조회 실패: {sheet_summary.get('error')}")
        lines.append("- 계좌 결산은 계속했지만 전략·주간 종합은 불완전합니다.")
    else:
        sheet_generated = str(sheet_summary.get("generated_at_utc") or "미확인")
        freshness = "최신" if sheet_summary.get("fresh") else "지연"
        lines.extend([
            f"- 읽은 탭: {sheet_summary.get('tabs_read', 0)}/3 (`summary`, `artifacts`, `history`)",
            f"- 최근 동기화: {sheet_generated} · {freshness}",
            f"- 분석 종목: {sheet_latest.get('symbol_count') or '미확인'}개",
            f"- 승인 국면: {sheet_latest.get('approved_situations') or '없음'}",
            f"- 검증 성과: 수익률 {_number(sheet_latest.get('combined_test_total_return_pct')):+.2f}%, MDD {_number(sheet_latest.get('combined_test_max_drawdown_pct')):+.2f}%, Sharpe {_number(sheet_latest.get('combined_test_sharpe')):.3f}, 거래 {_number(sheet_latest.get('combined_test_total_trades')):g}건",
            f"- 이력: 전체 {sheet_summary.get('history_count', 0)}회 · 이번 주 {sheet_summary.get('week_history_count', 0)}회",
            f"- 산출물 참조: {sheet_summary.get('artifact_count', 0)}개",
        ])
        if unmatched_sell_qty > 0:
            lines.append("- ⚠️ 현재 Sheets에는 실계좌 체결원가 탭이 없어 FIFO 미매칭 원가는 복원하지 못했습니다.")

    if now.weekday() == 4:
        lines.extend(["", f"## 주간 종합 — {week_start} ~ {week_end}"])
        lines.extend([
            f"- 자동체결: {len(week_orders)}건",
            f"- 매수대금: {week_buy_amount:,.0f}원",
            f"- 매도대금: {week_sell_amount:,.0f}원",
            f"- 실현손익(매칭 FIFO): {week_realized_total:+,.0f}원",
            f"- 원가 미매칭 매도: {week_unmatched_sell_qty:g}주",
            f"- Sheets 전략 파이프라인: {sheet_summary.get('week_history_count', 0)}회",
        ])
        if len(week_snapshots) >= 2:
            start_equity = _number((week_snapshots[0].get("account") or {}).get("total_equity"))
            end_equity = _number((week_snapshots[-1].get("account") or {}).get("total_equity"))
            lines.append(f"- 주간 평가자산 증감: {end_equity - start_equity:+,.0f}원 ({start_equity:,.0f} → {end_equity:,.0f}원)")
        else:
            lines.append("- 주간 평가자산 증감: 일별 결산 기준 스냅샷 부족으로 이번 주는 산출 불가")
        if week_unmatched_sell_qty > 0:
            lines.append("- 주간 손익 판정: 원가 미매칭분이 있어 보류")
        else:
            lines.append(f"- 주간 손익 판정: 매칭 가능한 자동원장 기준 {week_realized_total:+,.0f}원")

    lines.append("")
    if unmatched_sell_qty > 0:
        lines.append(f"⚠️ FIFO 원가 미매칭 매도 {unmatched_sell_qty:g}주: 수동 거래·기존 보유 원가가 자동원장에 없습니다.")
        verdict = "실현손익 원가가 불완전하므로 손익 판정을 보류합니다."
    elif total_pnl > 0:
        verdict = "자동원장 실현손익과 보유 누적 미실현손익의 합계가 양수입니다."
    elif total_pnl < 0:
        verdict = "자동원장 실현손익과 보유 누적 미실현손익의 합계가 음수입니다."
    else:
        verdict = "자동원장 실현손익과 보유 누적 미실현손익의 합계가 0원입니다."
    lines.append(f"판정: {verdict}")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
