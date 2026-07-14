"""KIS live-order status reconciliation and stale-unfilled management.

This module is deliberately conservative:
- Status inquiry is read-only and can run before each live-submit loop.
- Broker cancel is available only behind explicit env opt-in.
- Ledger state is append-only; callers decide duplicate blocking from the latest
  status per ledger key.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo
import json

from toss_alpha.connectors.kis_rate_limit import kis_post, kis_request
from toss_alpha.connectors.kis_token_cache import cached_kis_access_token
from toss_alpha.execution.live_ready import LiveExecutionConfig


KST = ZoneInfo("Asia/Seoul")
TERMINAL_STATUSES = {"FILLED", "CANCELED", "REJECTED", "EXPIRED", "SUPERSEDED", "PARTIALLY_FILLED_CANCELED"}
ACTIVE_STATUSES = {"PENDING_SUBMIT", "SUBMITTED", "PARTIALLY_FILLED", "UNKNOWN", "CANCEL_REQUESTED"}


@dataclass(frozen=True)
class KisOrderStatusClient:
    config: LiveExecutionConfig

    def token(self) -> str:
        if self.config.access_token:
            return self.config.access_token

        def fetch_token() -> dict[str, Any]:
            response = kis_post(
                f"{self.config.base_url}/oauth2/tokenP",
                headers={"Content-Type": "application/json"},
                json={
                    "grant_type": "client_credentials",
                    "appkey": self.config.app_key,
                    "appsecret": self.config.app_secret,
                },
                timeout=self.config.timeout,
            )
            if not response.ok:
                raise RuntimeError(f"token failed: HTTP {response.status_code} {response.text[:500]}")
            return response.json()

        return cached_kis_access_token(
            app_key=self.config.app_key or "",
            base_url=self.config.base_url,
            fetch_token=fetch_token,
        )

    def headers(self, tr_id: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token()}",
            "appkey": self.config.app_key or "",
            "appsecret": self.config.app_secret or "",
            "tr_id": tr_id,
            "custtype": self.config.kis_custtype,
            "Content-Type": "application/json",
        }

    def inquire_daily_fills(self, *, order_no: str, order_orgno: str | None, day: datetime) -> dict[str, Any]:
        """Read KIS daily order/fill status for one order number.

        KIS domestic stock daily fill inquiry endpoint is used as a status source.
        Field names vary slightly by environment, so parsing is intentionally
        tolerant and keeps raw payload in the audit result.
        """
        day_text = day.astimezone(KST).strftime("%Y%m%d")
        params = {
            "CANO": self.config.cano,
            "ACNT_PRDT_CD": self.config.account_product_code or "01",
            "INQR_STRT_DT": day_text,
            "INQR_END_DT": day_text,
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": "00",
            "ORD_GNO_BRNO": "",
            "ODNO": order_no,
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
            "EXCG_ID_DVSN_CD": "KRX",
        }
        response = kis_request(
            "GET",
            f"{self.config.base_url}/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            headers=self.headers("VTTC0081R" if self.config.kis_mock_trading else "TTTC0081R"),
            params=params,
            timeout=self.config.timeout,
        )
        try:
            body = response.json()
        except Exception:
            body = None
        if not response.ok:
            raise RuntimeError(f"order status failed: HTTP {response.status_code} {response.text[:500]}")
        return {"status_code": response.status_code, "json": body, "text": response.text}

    def cancel_order(self, *, order_no: str, order_orgno: str, quantity: str = "0") -> dict[str, Any]:
        """Cancel a KIS cash order. Caller must gate this with explicit opt-in."""
        payload = {
            "CANO": self.config.cano or "",
            "ACNT_PRDT_CD": self.config.account_product_code or "01",
            "KRX_FWDG_ORD_ORGNO": order_orgno,
            "ORGN_ODNO": order_no,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02",  # cancel
            "ORD_QTY": "0",
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y",
            "EXCG_ID_DVSN_CD": "KRX",
        }
        hash_response = kis_post(
            f"{self.config.base_url}{self.config.hashkey_endpoint_path}",
            headers={"appkey": self.config.app_key or "", "appsecret": self.config.app_secret or "", "Content-Type": "application/json"},
            json=payload,
            timeout=self.config.timeout,
        )
        if not hash_response.ok:
            raise RuntimeError(f"cancel hashkey failed: HTTP {hash_response.status_code} {hash_response.text[:500]}")
        hash_body = hash_response.json()
        response = kis_post(
            f"{self.config.base_url}/uapi/domestic-stock/v1/trading/order-rvsecncl",
            headers={**self.headers("VTTC0013U" if self.config.kis_mock_trading else "TTTC0013U"), "hashkey": str(hash_body.get("HASH") or hash_body.get("hash") or "")},
            json=payload,
            timeout=self.config.timeout,
        )
        try:
            body = response.json()
        except Exception:
            body = None
        return {"status_code": response.status_code, "ok": response.ok, "json": body, "text": response.text, "payload": payload}


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def latest_by_key(rows: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    latest: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        key = str(row.get("ledger_key") or "")
        if key:
            latest[key] = row
    return latest


def extract_kis_order_ids(result: Mapping[str, Any]) -> tuple[str | None, str | None]:
    body = result.get("json") if isinstance(result.get("json"), Mapping) else {}
    output = body.get("output") if isinstance(body, Mapping) and isinstance(body.get("output"), Mapping) else {}
    order_no = (
        output.get("ODNO")
        or output.get("odno")
        or result.get("ODNO")
        or result.get("order_no")
    )
    orgno = (
        output.get("KRX_FWDG_ORD_ORGNO")
        or output.get("ord_gno_brno")
        or result.get("KRX_FWDG_ORD_ORGNO")
        or result.get("order_orgno")
    )
    return (str(order_no) if order_no else None, str(orgno) if orgno else None)


def parse_status_payload(payload: Mapping[str, Any], *, order_no: str) -> dict[str, Any]:
    body = payload.get("json") if isinstance(payload.get("json"), Mapping) else {}
    records = []
    if isinstance(body, Mapping):
        for key in ("output1", "output", "result"):
            value = body.get(key)
            if isinstance(value, list):
                records = [item for item in value if isinstance(item, Mapping)]
                break
            if isinstance(value, Mapping):
                records = [value]
                break
    record = None
    for item in records:
        item_order_no = item.get("odno") or item.get("ODNO") or item.get("odno_no") or item.get("order_no")
        if item_order_no is not None and str(item_order_no) == str(order_no):
            record = item
            break
    if record is None:
        return {
            "status": "UNKNOWN",
            "order_qty": None,
            "filled_qty": None,
            "remaining_qty": None,
            "raw_record": {},
            "reason": "order_no_not_found_in_status_payload",
        }
    order_qty = _to_float(record.get("ord_qty") or record.get("ORD_QTY") or record.get("order_qty"))
    filled_qty = _to_float(
        record.get("tot_ccld_qty")
        or record.get("ccld_qty")
        or record.get("exec_qty")
        or record.get("filled_qty")
        or 0
    )
    remaining_qty = _to_float(
        record.get("rmn_qty")
        or record.get("ord_unprcs_qty")
        or record.get("unfilled_qty")
        or record.get("remaining_qty")
    )
    # Without an explicit broker remaining quantity or terminal status field, the
    # outcome is ambiguous. Never infer an active order from order_qty-filled_qty:
    # canceled/rejected rows commonly omit remaining_qty.
    status = "UNKNOWN"
    if order_qty is not None and filled_qty is not None and filled_qty >= order_qty and order_qty > 0:
        status = "FILLED"
    elif filled_qty and filled_qty > 0 and (remaining_qty or 0) > 0:
        status = "PARTIALLY_FILLED"
    elif filled_qty and filled_qty > 0 and remaining_qty == 0:
        status = "PARTIALLY_FILLED_CANCELED"
    elif remaining_qty is not None and remaining_qty > 0:
        status = "SUBMITTED"
    elif remaining_qty == 0 and (filled_qty or 0) == 0 and record:
        status = "CANCELED"
    return {
        "status": status,
        "order_qty": order_qty,
        "filled_qty": filled_qty,
        "remaining_qty": remaining_qty,
        "raw_record": dict(record),
    }


def manage_submitted_order_ledger(
    *,
    ledger_path: Path,
    env: Mapping[str, str] | None,
    desired_order_keys: set[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reconcile latest SUBMITTED/PARTIALLY_FILLED KIS ledger entries.

    Appends status rows to the same ledger. If cancellation is explicitly enabled
    and a submitted order is older than the configured grace period, it attempts
    to cancel the remaining order quantity.
    """
    now = now or datetime.now(timezone.utc)
    source = env or {}
    enabled = _env_true(source.get("TOSS_ORDER_RECONCILE_ENABLED"), default=True)
    cancel_enabled = _env_true(source.get("TOSS_CANCEL_STALE_UNFILLED_ENABLED"), default=False)
    invalidated_cancel_enabled = _env_true(source.get("TOSS_CANCEL_INVALIDATED_ORDERS_ENABLED"), default=False)
    superseded_sell_cancel_enabled = _env_true(source.get("TOSS_CANCEL_SUPERSEDED_SELL_ENABLED"), default=False)
    stale_minutes = _env_float(source, "TOSS_UNFILLED_CANCEL_AFTER_MINUTES", 60.0)
    audit: dict[str, Any] = {
        "enabled": enabled,
        "cancel_enabled": cancel_enabled,
        "invalidated_cancel_enabled": invalidated_cancel_enabled,
        "superseded_sell_cancel_enabled": superseded_sell_cancel_enabled,
        "stale_minutes": stale_minutes,
        "checked_count": 0,
        "updated_count": 0,
        "cancel_attempted_count": 0,
        "invalidated_cancel_attempted_count": 0,
        "superseded_sell_cancel_attempted_count": 0,
        "cancel_reasons": {},
        "reprice_remaining_by_key": {},
        "errors": [],
    }
    if not enabled:
        audit["status"] = "DISABLED"
        return audit
    config = LiveExecutionConfig.from_env(source)
    if config.provider != "kis":
        audit["status"] = "SKIPPED_PROVIDER"
        audit["provider"] = config.provider
        return audit
    rows = read_jsonl(ledger_path)
    latest = latest_by_key(rows)
    first_submitted_by_key: dict[str, datetime] = {}
    for historical in rows:
        historical_key = str(historical.get("ledger_key") or "")
        if not historical_key or str(historical.get("status") or "") not in {"PENDING_SUBMIT", "SUBMITTED", "PARTIALLY_FILLED", "UNKNOWN", "CANCEL_REQUESTED"}:
            continue
        historical_at = _parse_dt(str(historical.get("first_submitted_at") or historical.get("timestamp") or ""))
        if historical_at is not None and (historical_key not in first_submitted_by_key or historical_at < first_submitted_by_key[historical_key]):
            first_submitted_by_key[historical_key] = historical_at
    active_rows = [row for row in latest.values() if str(row.get("status") or "") in ACTIVE_STATUSES]
    if not active_rows:
        audit["status"] = "NO_ACTIVE_ORDERS"
        return audit
    client = KisOrderStatusClient(config)
    for row in active_rows:
        audit["checked_count"] += 1
        key = str(row.get("ledger_key"))
        result = row.get("result") if isinstance(row.get("result"), Mapping) else row
        order_no, orgno = extract_kis_order_ids(result)
        if not order_no:
            append_jsonl(
                ledger_path,
                {
                    **{k: row.get(k) for k in ("symbol", "side", "quantity", "limit_price", "intent_id") if row.get(k) is not None},
                    "ledger_key": key,
                    "status": "UNKNOWN",
                    "timestamp": now.isoformat(),
                    "reason": "missing_kis_order_no",
                    "recovery_required": True,
                },
            )
            audit["updated_count"] += 1
            audit["errors"].append({"ledger_key": key, "error": "missing_kis_order_no_recovery_required"})
            continue
        try:
            first_submitted_at = first_submitted_by_key.get(key) or _parse_dt(str(row.get("first_submitted_at") or row.get("timestamp") or "")) or now
            status_payload = client.inquire_daily_fills(order_no=order_no, order_orgno=orgno, day=first_submitted_at)
            parsed = parse_status_payload(status_payload, order_no=order_no)
            broker_status = str(parsed["status"])
            prior_status = str(row.get("status") or "")
            replacement_qty = _to_float(row.get("replacement_qty"))
            prior_cancel_reason = str(row.get("cancel_reason") or "") or None
            if prior_status == "CANCEL_REQUESTED" and broker_status not in TERMINAL_STATUSES:
                status = "CANCEL_REQUESTED"
            else:
                status = broker_status
            ledger_row = {
                "ledger_key": key,
                "status": status,
                "timestamp": now.isoformat(),
                "first_submitted_at": first_submitted_at.isoformat(),
                "order_no": order_no,
                "order_orgno": orgno,
                "broker_status": parsed,
            }
            if prior_status == "CANCEL_REQUESTED":
                ledger_row["replacement_qty"] = replacement_qty
                ledger_row["cancel_reason"] = prior_cancel_reason
            append_jsonl(ledger_path, ledger_row)
            audit["updated_count"] += 1
            if prior_status == "CANCEL_REQUESTED":
                if status in {"CANCELED", "PARTIALLY_FILLED_CANCELED"} and prior_cancel_reason not in {"current_buy_signal_removed", "superseded_by_new_sell_intent"}:
                    confirmed_replacement = replacement_qty
                    parsed_order_qty = _to_float(parsed.get("order_qty"))
                    parsed_filled_qty = _to_float(parsed.get("filled_qty"))
                    if parsed_order_qty is not None and parsed_filled_qty is not None:
                        confirmed_replacement = max(0.0, parsed_order_qty - parsed_filled_qty)
                    if confirmed_replacement is not None and confirmed_replacement > 0:
                        audit["reprice_remaining_by_key"][key] = int(confirmed_replacement)
                continue
            age_minutes = max(0.0, (now - first_submitted_at).total_seconds() / 60.0)
            remaining_qty = parsed.get("remaining_qty")
            is_buy_key = key.rsplit(":", 1)[-1].upper() == "BUY"
            is_sell_key = key.rsplit(":", 1)[-1].upper() == "SELL"
            current_buy_signal_removed = (
                invalidated_cancel_enabled
                and desired_order_keys is not None
                and is_buy_key
                and key not in desired_order_keys
            )
            key_parts = key.rsplit(":", 2)
            sell_suffix = f":{key_parts[-2]}:SELL" if len(key_parts) == 3 else ""
            superseded_sell = (
                superseded_sell_cancel_enabled
                and desired_order_keys is not None
                and is_sell_key
                and key not in desired_order_keys
                and bool(sell_suffix)
                and any(desired_key.endswith(sell_suffix) for desired_key in desired_order_keys)
            )
            stale_unfilled = cancel_enabled and age_minutes >= stale_minutes
            if (current_buy_signal_removed or superseded_sell or stale_unfilled) and status in {"SUBMITTED", "PARTIALLY_FILLED", "UNKNOWN"} and orgno:
                cancel_reason = (
                    "current_buy_signal_removed" if current_buy_signal_removed
                    else ("superseded_by_new_sell_intent" if superseded_sell else "stale_unfilled_timeout")
                )
                cancel = client.cancel_order(order_no=order_no, order_orgno=orgno, quantity=str(int(remaining_qty or 0)))
                cancel_ok = _kis_rt_cd_ok(cancel.get("json"))
                # A successful cancel API response means accepted/requested, not
                # broker-terminal. Keep the key active until a later status
                # inquiry proves remaining quantity is zero.
                cancel_status = "CANCEL_REQUESTED" if cancel_ok else status
                append_jsonl(
                    ledger_path,
                    {
                        "ledger_key": key,
                        "status": cancel_status,
                        "timestamp": now.isoformat(),
                        "first_submitted_at": first_submitted_at.isoformat(),
                        "order_no": order_no,
                        "order_orgno": orgno,
                        "replacement_qty": int(float(remaining_qty)) if remaining_qty is not None and float(remaining_qty) > 0 else None,
                        "cancel_reason": cancel_reason,
                        "cancel_rejected": not cancel_ok,
                        "cancel_result": _redact_cancel(cancel),
                    },
                )
                audit["cancel_attempted_count"] += 1
                audit["cancel_reasons"][key] = cancel_reason
                if current_buy_signal_removed:
                    audit["invalidated_cancel_attempted_count"] += 1
                if superseded_sell:
                    audit["superseded_sell_cancel_attempted_count"] += 1
        except Exception as exc:
            audit["errors"].append({"ledger_key": key, "error": repr(exc)})
    audit["status"] = "OK" if not audit["errors"] else "PARTIAL_ERROR"
    return audit


def _env_true(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(env: Mapping[str, str], key: str, default: float) -> float:
    try:
        return float(env.get(key, default))
    except (TypeError, ValueError):
        return default


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_dt(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _kis_rt_cd_ok(body: Any) -> bool:
    return isinstance(body, Mapping) and str(body.get("rt_cd", "")).strip() == "0"


def _redact_cancel(result: Mapping[str, Any]) -> dict[str, Any]:
    clean = dict(result)
    payload = clean.get("payload")
    if isinstance(payload, Mapping):
        clean["payload"] = {k: ("[REDACTED]" if k in {"CANO", "ACNT_PRDT_CD"} else v) for k, v in payload.items()}
    return clean
