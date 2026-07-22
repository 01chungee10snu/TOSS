#!/usr/bin/env python3
"""Emit a Telegram-ready message only for new BUY/SELL submit/fill ledger events.

Empty stdout means no notification. State is persisted as byte offset plus event IDs,
so historical rows and repeated reconciliation rows are not re-alerted.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

LEDGER = Path(os.environ.get(
    "TOSS_TRADE_ALERT_LEDGER",
    "/Users/01chungee10/Github/TOSS/reports/harness/live_order_ledger.jsonl",
))
STATE = Path(os.environ.get(
    "TOSS_TRADE_ALERT_STATE",
    "/Users/01chungee10/Github/TOSS/reports/harness/trade_alert_watch_state.json",
))
MAX_SEEN = 2000


def load_state() -> dict[str, Any] | None:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("offset"), int):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return None


def save_state(offset: int, seen: list[str]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(STATE.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"offset": offset, "seen": seen[-MAX_SEEN:]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, STATE)


def infer_side(row: dict[str, Any]) -> str | None:
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    side = result.get("side") or row.get("side")
    if side in {"BUY", "SELL"}:
        return side
    key = str(row.get("ledger_key", ""))
    tail = key.rsplit(":", 1)[-1]
    return tail if tail in {"BUY", "SELL"} else None


def infer_symbol(row: dict[str, Any]) -> str:
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    broker = row.get("broker_status") if isinstance(row.get("broker_status"), dict) else {}
    raw = broker.get("raw_record") if isinstance(broker.get("raw_record"), dict) else {}
    symbol = result.get("symbol") or row.get("symbol") or raw.get("pdno")
    if symbol:
        return str(symbol)
    key = str(row.get("ledger_key", ""))
    parts = key.split(":")
    return parts[-2] if len(parts) >= 2 else "미상"


def event_id(row: dict[str, Any], status: str) -> str:
    broker = row.get("broker_status") if isinstance(row.get("broker_status"), dict) else {}
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    payload = result.get("json") if isinstance(result.get("json"), dict) else {}
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    order_no = row.get("order_no") or output.get("ODNO") or ""
    return f"{row.get('ledger_key', '')}|{status}|{order_no}"


def format_event(row: dict[str, Any], status: str, side: str) -> str:
    symbol = infer_symbol(row)
    action = "매수" if side == "BUY" else "매도"
    broker = row.get("broker_status") if isinstance(row.get("broker_status"), dict) else {}
    raw = broker.get("raw_record") if isinstance(broker.get("raw_record"), dict) else {}
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    payload = result.get("json") if isinstance(result.get("json"), dict) else {}
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    order_no = row.get("order_no") or output.get("ODNO") or "미상"
    timestamp = str(row.get("timestamp", ""))

    if status == "FILLED":
        qty = broker.get("filled_qty") or raw.get("tot_ccld_qty") or "미상"
        price = raw.get("avg_prvs") or "미상"
        return f"✅ **자동 {action} 체결**\n- 종목: `{symbol}`\n- 수량: {qty}주\n- 체결가: {price}원\n- 주문번호: `{order_no}`\n- 시각(UTC): {timestamp}"

    return f"🔔 **자동 {action} 주문 전송**\n- 종목: `{symbol}`\n- 주문번호: `{order_no}`\n- 상태: 체결 대기\n- 시각(UTC): {timestamp}"


def main() -> None:
    if not LEDGER.exists():
        save_state(0, [])
        return

    size = LEDGER.stat().st_size
    state = load_state()
    if state is None:
        # First activation: start at EOF so historical trades are never replayed.
        save_state(size, [])
        return

    offset = int(state.get("offset", 0))
    seen = [str(x) for x in state.get("seen", [])]
    seen_set = set(seen)
    if offset < 0 or offset > size:  # ledger rotated/truncated
        offset = 0

    events: list[str] = []
    with LEDGER.open("r", encoding="utf-8") as fh:
        fh.seek(offset)
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            status = str(row.get("status", ""))
            if status not in {"SUBMITTED", "FILLED"}:
                continue
            side = infer_side(row)
            if side not in {"BUY", "SELL"}:
                continue
            eid = event_id(row, status)
            if eid in seen_set:
                continue
            seen.append(eid)
            seen_set.add(eid)
            events.append(format_event(row, status, side))
        new_offset = fh.tell()

    save_state(new_offset, seen)
    if events:
        print("\n\n".join(events))


if __name__ == "__main__":
    main()
