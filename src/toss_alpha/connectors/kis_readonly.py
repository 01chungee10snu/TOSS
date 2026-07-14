"""Read-only Korea Investment & Securities (KIS) Open API connector."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from toss_alpha.connectors.kis_rate_limit import kis_post, kis_request
from toss_alpha.connectors.kis_token_cache import cached_kis_access_token
from toss_alpha.data.schema import AccountSnapshot, PositionSnapshot, Quote

LIVE_BASE_URL = "https://openapi.koreainvestment.com:9443"
MOCK_BASE_URL = "https://openapivts.koreainvestment.com:29443"
RATE_LIMIT_HEADERS = (
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
    "Retry-After",
    "tr_id",
    "gt_uid",
    "tr_cont",
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
    quote_path: str = "/uapi/domestic-stock/v1/quotations/inquire-price"
    orderbook_path: str = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
    balance_tr_id: str | None = None
    quote_tr_id: str | None = None
    orderbook_tr_id: str | None = None

    def token(self) -> str:
        def fetch_token() -> dict[str, Any]:
            response = kis_post(
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
            return response.json()

        return cached_kis_access_token(
            app_key=self.app_key,
            base_url=self._resolved_base_url(),
            fetch_token=fetch_token,
        )

    def _resolved_base_url(self) -> str:
        if self.base_url:
            return self.base_url
        return MOCK_BASE_URL if self.mock_trading else LIVE_BASE_URL

    def _default_balance_tr_id(self) -> str:
        return "VTTC8434R" if self.mock_trading else "TTTC8434R"

    def _default_quote_tr_id(self) -> str:
        return "FHKST01010100"

    def _default_orderbook_tr_id(self) -> str:
        return "FHKST01010200"

    def _headers(self, *, tr_id: str | None = None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id or self.balance_tr_id or self._default_balance_tr_id(),
            "custtype": "P",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        tr_id: str | None = None,
        tr_cont: str = "",
    ) -> dict[str, Any]:
        headers = self._headers(tr_id=tr_id)
        if tr_cont:
            headers["tr_cont"] = tr_cont
        response = kis_request(
            method,
            f"{self._resolved_base_url()}{path}",
            headers=headers,
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
        if isinstance(payload, dict) and "rt_cd" in payload and str(payload.get("rt_cd")) != "0":
            msg_cd = str(payload.get("msg_cd") or "unknown")
            msg = str(payload.get("msg1") or "KIS business error")
            raise RuntimeError(f"request failed: KIS {msg_cd} {msg[:300]}")
        return result

    def balance(self, *, query: dict[str, Any] | None = None, tr_cont: str = "") -> dict[str, Any]:
        """Return one official KIS domestic-stock balance page."""
        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.account_product_code,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "01",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        if query:
            params.update(query)
        return self._request("GET", self.balance_path, params=params, tr_cont=tr_cont)

    def balance_all(self, *, max_pages: int = 10) -> dict[str, Any]:
        """Fetch and merge official KIS balance continuation pages."""
        merged_positions: list[dict[str, Any]] = []
        summary: Any = []
        query: dict[str, Any] = {}
        tr_cont = ""
        latest: dict[str, Any] | None = None
        for _ in range(max_pages):
            latest = self.balance(query=query, tr_cont=tr_cont)
            payload = latest.get("json") if isinstance(latest.get("json"), dict) else {}
            rows = payload.get("output1")
            if isinstance(rows, list):
                merged_positions.extend(item for item in rows if isinstance(item, dict))
            if payload.get("output2") is not None:
                summary = payload.get("output2")
            continuation = str(latest.get("headers", {}).get("tr_cont") or "").upper()
            if continuation not in {"M", "F"}:
                break
            fk100 = str(payload.get("ctx_area_fk100") or "")
            nk100 = str(payload.get("ctx_area_nk100") or "")
            if not fk100 and not nk100:
                raise RuntimeError("balance continuation advertised without CTX_AREA keys")
            query = {"CTX_AREA_FK100": fk100, "CTX_AREA_NK100": nk100}
            tr_cont = "N"
        else:
            raise RuntimeError(f"balance pagination exceeded max_pages={max_pages}")
        if latest is None:
            raise RuntimeError("balance pagination returned no response")
        merged_payload = dict(latest.get("json") or {})
        merged_payload["output1"] = merged_positions
        merged_payload["output2"] = summary
        return {**latest, "json": merged_payload}

    def quote(self, symbol: str) -> dict[str, Any]:
        """Return KIS domestic stock current-price payload for a symbol.

        This is read-only market data. It never places or amends orders.
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": str(symbol).strip().zfill(6),
        }
        return self._request(
            "GET",
            self.quote_path,
            params=params,
            tr_id=self.quote_tr_id or self._default_quote_tr_id(),
        )

    def orderbook(self, symbol: str) -> dict[str, Any]:
        """Return KIS domestic stock order-book payload (read-only)."""
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": str(symbol).strip().zfill(6),
        }
        return self._request(
            "GET",
            self.orderbook_path,
            params=params,
            tr_id=self.orderbook_tr_id or self._default_orderbook_tr_id(),
        )

    def quote_snapshot(self, symbol: str) -> Quote:
        quote_payload = self.quote(symbol)["json"] or {}
        quote_record = _first_record(quote_payload)
        orderbook_payload = self.orderbook(symbol)["json"] or {}
        orderbook_record = orderbook_payload.get("output1")
        if not isinstance(orderbook_record, dict):
            orderbook_record = _first_record(orderbook_payload)
        return Quote(
            symbol=str(symbol).strip().zfill(6),
            timestamp=datetime.now(timezone.utc),
            last=_to_float(quote_record.get("stck_prpr")) or 0.0,
            bid=_to_float(orderbook_record.get("bidp1")),
            ask=_to_float(orderbook_record.get("askp1")),
            volume=_to_float(quote_record.get("acml_vol")),
            session_high=_to_float(quote_record.get("stck_hgpr")),
            source="kis",
        )

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
        payload = self.balance_all()["json"] or {}
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
