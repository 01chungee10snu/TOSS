"""Read-only Korea Investment & Securities (KIS) Open API connector."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from toss_alpha.data.schema import AccountSnapshot, PositionSnapshot

LIVE_BASE_URL = "https://openapi.koreainvestment.com:9443"
MOCK_BASE_URL = "https://openapivts.koreainvestment.com:29443"
RATE_LIMIT_HEADERS = (
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
    "Retry-After",
    "tr_id",
    "gt_uid",
)


@dataclass(frozen=True)
class KisReadOnlyClient:
    app_key: str
    app_secret: str
    cano: str
    account_product_code: str = "01"
    mock_trading: bool = False
    base_url: str | None = None
    timeout: int = 20
    balance_path: str = "/uapi/domestic-stock/v1/trading/inquire-balance"
    balance_tr_id: str | None = None

    def token(self) -> str:
        response = requests.post(
            f"{self._resolved_base_url()}/oauth2/tokenP",
            headers={"Content-Type": "application/json"},
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            timeout=self.timeout,
        )
        if not response.ok:
            raise RuntimeError(f"token failed: HTTP {response.status_code} {response.text[:500]}")
        data = response.json()
        access_token = data.get("access_token")
        if not access_token:
            raise RuntimeError(f"token response has no access_token: {data}")
        return access_token

    def _resolved_base_url(self) -> str:
        if self.base_url:
            return self.base_url
        return MOCK_BASE_URL if self.mock_trading else LIVE_BASE_URL

    def _default_balance_tr_id(self) -> str:
        return "VTTC8434R" if self.mock_trading else "TTTC8434R"

    def _headers(self, *, tr_id: str | None = None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id or self.balance_tr_id or self._default_balance_tr_id(),
            "custtype": "P",
        }

    def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, tr_id: str | None = None) -> dict[str, Any]:
        response = requests.request(
            method,
            f"{self._resolved_base_url()}{path}",
            headers=self._headers(tr_id=tr_id),
            params=params,
            timeout=self.timeout,
        )
        try:
            payload = response.json()
        except Exception:
            payload = None
        result = {
            "status_code": response.status_code,
            "ok": response.ok,
            "headers": {h: response.headers[h] for h in RATE_LIMIT_HEADERS if h in response.headers},
            "json": payload,
            "text": response.text,
        }
        if not response.ok:
            raise RuntimeError(f"request failed: HTTP {response.status_code} {response.text[:500]}")
        return result

    def balance(self, *, query: dict[str, Any] | None = None) -> dict[str, Any]:
        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.account_product_code,
        }
        if query:
            params.update(query)
        return self._request("GET", self.balance_path, params=params)

    def account_snapshot(self) -> AccountSnapshot:
        payload = self.balance()["json"] or {}
        record = _first_record(payload)
        return AccountSnapshot(
            account_id=f"{self.cano}-{self.account_product_code}",
            cash=_to_float(
                record.get("dnca_tot_amt")
                or record.get("cash")
                or record.get("cash_balance")
                or record.get("ord_psbl_cash")
            ),
            buying_power=_to_float(
                record.get("ord_psbl_cash")
                or record.get("buying_power")
                or record.get("cash")
            ),
            total_equity=_to_float(
                record.get("tot_evlu_amt")
                or record.get("scts_evlu_amt")
                or record.get("total_equity")
            ),
            source="kis",
        )

    def position_snapshots(self) -> list[PositionSnapshot]:
        payload = self.balance()["json"] or {}
        records = _positions_list(payload)
        positions: list[PositionSnapshot] = []
        for record in records:
            symbol = str(record.get("pdno") or record.get("symbol") or record.get("code") or "").strip()
            if not symbol:
                continue
            positions.append(
                PositionSnapshot(
                    symbol=symbol,
                    quantity=float(record.get("hldg_qty") or record.get("quantity") or 0.0),
                    sellable_quantity=_to_float(record.get("ord_psbl_qty") or record.get("sellable_quantity")),
                    avg_price=_to_float(record.get("pchs_avg_pric") or record.get("avg_price")),
                    market_value=_to_float(record.get("evlu_amt") or record.get("market_value")),
                    unrealized_pnl=_to_float(record.get("evlu_pfls_amt") or record.get("unrealized_pnl")),
                    source="kis",
                )
            )
        return positions


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _first_record(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("output2", "output", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return value[0] if value else {}
        if isinstance(value, dict):
            return value
    return payload if isinstance(payload, dict) else {}


def _positions_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("output1", "positions", "holdings", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []
