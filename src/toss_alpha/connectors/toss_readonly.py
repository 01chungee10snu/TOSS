"""Read-only Toss Securities Open API connector."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

BASE_URL = "https://openapi.tossinvest.com"
RATE_LIMIT_HEADERS = (
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
    "Retry-After",
    "X-Request-Id",
)


@dataclass(frozen=True)
class TossReadOnlyClient:
    client_id: str
    client_secret: str
    account_seq: str | None = None
    base_url: str = BASE_URL
    timeout: int = 20

    def token(self) -> str:
        response = requests.post(
            f"{self.base_url}/oauth2/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
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

    def _headers(self, *, account: bool = False) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token()}"}
        if account:
            if not self.account_seq:
                raise ValueError("account_seq is required for account endpoints")
            headers["X-Tossinvest-Account"] = self.account_seq
        return headers

    def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, account: bool = False) -> dict[str, Any]:
        response = requests.request(
            method,
            f"{self.base_url}{path}",
            headers=self._headers(account=account),
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

    def stocks(self, symbols: str) -> dict[str, Any]:
        return self._request("GET", "/api/v1/stocks", params={"symbols": symbols})

    def prices(self, symbols: str) -> dict[str, Any]:
        return self._request("GET", "/api/v1/prices", params={"symbols": symbols})

    def candles(self, symbol: str, interval: str = "1D") -> dict[str, Any]:
        return self._request("GET", "/api/v1/candles", params={"symbol": symbol, "interval": interval})

    def accounts(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/accounts", account=True)

    def holdings(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/holdings", account=True)
