"""Guarded live execution readiness layer.

This module is intentionally fail-closed. It can prepare and dry-run a payload now,
and it can submit only after explicit double opt-in plus exact confirmation.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

import requests

from toss_alpha.data.schema import OrderIntent, RiskDecision
from toss_alpha.risk import RiskPolicy

TOSS_BASE_URL = "https://openapi.tossinvest.com"
KIS_LIVE_BASE_URL = "https://openapi.koreainvestment.com:9443"
KIS_MOCK_BASE_URL = "https://openapivts.koreainvestment.com:29443"
REAL_ORDER_CONFIRMATION_PHRASE = "I UNDERSTAND THIS IS A REAL ORDER"


@dataclass(frozen=True)
class LiveExecutionConfig:
    provider: str = "tossinvest"
    client_id: str | None = None
    client_secret: str | None = None
    app_key: str | None = None
    app_secret: str | None = None
    account_seq: str | None = None
    cano: str | None = None
    account_product_code: str | None = None
    order_endpoint_path: str | None = None
    access_token: str | None = None
    live_trading_env_enabled: bool = False
    base_url: str = TOSS_BASE_URL
    timeout: int = 20
    confirmation_phrase: str = REAL_ORDER_CONFIRMATION_PHRASE
    hashkey_endpoint_path: str = "/uapi/hashkey"
    kis_order_tr_id_buy: str | None = None
    kis_order_tr_id_sell: str | None = None
    kis_mock_trading: bool = False
    kis_custtype: str = "P"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LiveExecutionConfig":
        source = os.environ if env is None else env
        provider = (source.get("BROKER_PROVIDER") or ("kis" if source.get("KIS_APP_KEY") else "tossinvest")).strip().lower()
        if provider == "kis":
            mock_trading = source.get("KIS_MOCK_TRADING", "").lower() == "true"
            cano, product_code = _resolve_kis_account(source)
            return cls(
                provider="kis",
                app_key=_blank_to_none(source.get("KIS_APP_KEY")),
                app_secret=_blank_to_none(source.get("KIS_APP_SECRET")),
                cano=cano,
                account_product_code=product_code,
                order_endpoint_path=_blank_to_none(source.get("KIS_LIVE_ORDER_ENDPOINT")) or "/uapi/domestic-stock/v1/trading/order-cash",
                access_token=_blank_to_none(source.get("KIS_ACCESS_TOKEN")),
                live_trading_env_enabled=source.get("KIS_LIVE_TRADING_ENABLED", "").lower() == "true",
                base_url=_blank_to_none(source.get("KIS_BASE_URL")) or (KIS_MOCK_BASE_URL if mock_trading else KIS_LIVE_BASE_URL),
                confirmation_phrase=source.get("KIS_REAL_ORDER_CONFIRMATION", REAL_ORDER_CONFIRMATION_PHRASE),
                hashkey_endpoint_path=_blank_to_none(source.get("KIS_HASHKEY_ENDPOINT")) or "/uapi/hashkey",
                kis_order_tr_id_buy=_blank_to_none(source.get("KIS_ORDER_TR_ID_BUY")) or ("VTTC0802U" if mock_trading else "TTTC0802U"),
                kis_order_tr_id_sell=_blank_to_none(source.get("KIS_ORDER_TR_ID_SELL")) or ("VTTC0801U" if mock_trading else "TTTC0801U"),
                kis_mock_trading=mock_trading,
                kis_custtype=source.get("KIS_CUSTTYPE", "P"),
            )
        return cls(
            provider="tossinvest",
            client_id=_blank_to_none(source.get("TOSSINVEST_CLIENT_ID")),
            client_secret=_blank_to_none(source.get("TOSSINVEST_CLIENT_SECRET")),
            account_seq=_blank_to_none(source.get("TOSSINVEST_ACCOUNT_SEQ")),
            order_endpoint_path=_blank_to_none(source.get("TOSSINVEST_LIVE_ORDER_ENDPOINT")),
            access_token=_blank_to_none(source.get("TOSSINVEST_ACCESS_TOKEN")),
            live_trading_env_enabled=source.get("TOSSINVEST_LIVE_TRADING_ENABLED", "").lower() == "true",
            base_url=_blank_to_none(source.get("TOSSINVEST_BASE_URL")) or TOSS_BASE_URL,
            confirmation_phrase=source.get("TOSSINVEST_REAL_ORDER_CONFIRMATION", REAL_ORDER_CONFIRMATION_PHRASE),
        )


def _valid_toss_order_endpoint_path(path: str | None) -> bool:
    return path == "/api/v1/orders"


def _blank_to_none(value: str | None) -> str | None:
    if value is None or value.strip() == "":
        return None
    return value.strip()


def _resolve_kis_account(source: Mapping[str, str]) -> tuple[str | None, str | None]:
    cano = _blank_to_none(source.get("KIS_CANO"))
    product_code = _blank_to_none(source.get("KIS_ACNT_PRDT_CD") or source.get("KIS_ACCOUNT_PRODUCT_CODE") or source.get("KIS_ACCOUNT_CODE"))
    if cano and product_code:
        return cano, product_code
    account_no = _blank_to_none(source.get("KIS_ACCOUNT_NO"))
    if account_no and len(account_no) >= 10:
        return account_no[:8], account_no[8:]
    return cano, product_code


def live_readiness(env: Mapping[str, str] | None = None, policy: RiskPolicy | None = None) -> dict[str, Any]:
    config = LiveExecutionConfig.from_env(env)
    policy = policy or RiskPolicy()
    missing: list[str] = []
    if not policy.live_trading_enabled:
        missing.append("live_trading_disabled")
    if not config.live_trading_env_enabled:
        missing.append("env_live_trading_not_enabled")
    if config.provider == "kis":
        if not (config.app_key and config.app_secret):
            missing.append("app_credentials")
        if not config.cano:
            missing.append("cano")
        if not config.account_product_code:
            missing.append("account_product_code")
        if not config.order_endpoint_path:
            missing.append("order_endpoint_path")
    else:
        if not (config.client_id and config.client_secret):
            missing.append("client_credentials")
        if not config.account_seq:
            missing.append("account_seq")
        if not config.order_endpoint_path:
            missing.append("order_endpoint_path")
        elif not _valid_toss_order_endpoint_path(config.order_endpoint_path):
            missing.append("unconfirmed_toss_order_endpoint_path")
    return {
        "provider": config.provider,
        "ready": not missing,
        "missing": missing,
        "dry_run_available": _dry_run_available(config),
        "requires_confirmation_phrase": config.confirmation_phrase,
        "default_mode": "BLOCK_UNLESS_DOUBLE_OPT_IN",
    }


def _dry_run_available(config: LiveExecutionConfig) -> bool:
    if config.provider == "kis":
        return bool(config.app_key and config.app_secret and config.cano and config.account_product_code)
    return bool(config.client_id and config.client_secret and config.account_seq)


def build_order_payload(intent: OrderIntent) -> dict[str, Any]:
    return {
        "symbol": intent.symbol,
        "side": intent.side,
        "notional_krw": intent.notional_krw,
        "quantity": intent.quantity,
        "order_type": intent.order_type,
        "limit_price": intent.limit_price,
        "time_in_force": intent.time_in_force,
        "client_order_id": intent.intent_id,
    }


@dataclass(frozen=True)
class GuardedLiveExecutor:
    config: LiveExecutionConfig
    policy: RiskPolicy

    def submit_manual_draft(
        self,
        intent: OrderIntent,
        risk_decision: RiskDecision,
        *,
        confirmation_phrase: str,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        payload = self._build_submission_payload(intent)
        violations = self._blocking_violations(risk_decision, intent=intent, confirmation_phrase=confirmation_phrase)
        if dry_run:
            return {
                "status": "DRY_RUN" if not risk_decision.violations else "BLOCK",
                "not_submitted": True,
                "payload": payload,
                "violations": violations,
                "mode": "manual_approved_live_ready_dry_run",
                "provider": self.config.provider,
            }
        if violations:
            return {"status": "BLOCK", "not_submitted": True, "payload": payload, "violations": violations, "provider": self.config.provider}

        response = requests.post(
            f"{self.config.base_url}{self.config.order_endpoint_path}",
            headers=self._submission_headers(intent=intent, payload=payload),
            json=payload,
            timeout=self.config.timeout,
        )
        try:
            body = response.json()
        except Exception:
            body = None
        result = {
            "provider": self.config.provider,
            "status": "SUBMITTED" if response.ok else "REJECTED",
            "status_code": response.status_code,
            "headers": {"X-Request-Id": response.headers.get("X-Request-Id")} if response.headers.get("X-Request-Id") else {},
            "json": body,
            "text": response.text,
        }
        if not response.ok:
            result["violations"] = ["broker_rejected_order"]
        return result

    def _build_submission_payload(self, intent: OrderIntent) -> dict[str, Any]:
        if self.config.provider == "kis":
            return {
                "CANO": self.config.cano or "",
                "ACNT_PRDT_CD": self.config.account_product_code or "",
                "PDNO": intent.symbol,
                "ORD_DVSN": "00" if intent.order_type.upper() == "LIMIT" else "01",
                "ORD_QTY": _quantity_text(intent.quantity),
                "ORD_UNPR": _price_text(intent),
            }
        # ── Toss Securities official spec ────────────────────
        # POST /api/v1/orders
        # { symbol, side, orderType, quantity, price }
        payload: dict[str, Any] = {
            "symbol": intent.symbol,
            "side": intent.side,
            "orderType": intent.order_type.upper(),
        }
        if intent.quantity is not None:
            payload["quantity"] = intent.quantity
        if intent.order_type.upper() == "LIMIT" and intent.limit_price is not None:
            payload["price"] = intent.limit_price
        return payload

    def _submission_headers(self, *, intent: OrderIntent, payload: dict[str, Any]) -> dict[str, str]:
        if self.config.provider == "kis":
            return {
                "Authorization": f"Bearer {self._access_token()}",
                "appkey": self.config.app_key or "",
                "appsecret": self.config.app_secret or "",
                "tr_id": self._kis_tr_id(intent.side),
                "custtype": self.config.kis_custtype,
                "hashkey": self._hashkey(payload),
                "Content-Type": "application/json",
            }
        return {
            "Authorization": f"Bearer {self._access_token()}",
            "X-Tossinvest-Account": self.config.account_seq or "",
            "Content-Type": "application/json",
        }

    def _blocking_violations(self, risk_decision: RiskDecision, *, intent: OrderIntent, confirmation_phrase: str) -> list[str]:
        violations = list(risk_decision.violations)
        if not risk_decision.allow:
            violations.append("risk_decision_blocked")
        if not self.policy.live_trading_enabled:
            violations.append("live_trading_disabled")
        if not self.config.live_trading_env_enabled:
            violations.append("env_live_trading_not_enabled")
        if not self.config.order_endpoint_path:
            violations.append("order_endpoint_path_missing")
        if confirmation_phrase != self.config.confirmation_phrase:
            violations.append("real_order_confirmation_phrase_mismatch")
        if self.config.provider == "kis":
            if not self.config.cano:
                violations.append("cano_missing")
            if not self.config.account_product_code:
                violations.append("account_product_code_missing")
            if intent.quantity is None:
                violations.append("kis_requires_explicit_quantity")
            if intent.order_type.upper() == "LIMIT" and intent.limit_price is None:
                violations.append("kis_limit_order_requires_limit_price")
        else:
            if not self.config.account_seq:
                violations.append("account_seq_missing")
            if not _valid_toss_order_endpoint_path(self.config.order_endpoint_path):
                violations.append("unconfirmed_toss_order_endpoint_path")
        return list(dict.fromkeys(violations))

    def _kis_tr_id(self, side: str) -> str:
        return (self.config.kis_order_tr_id_buy if side.upper() == "BUY" else self.config.kis_order_tr_id_sell) or ""

    def _hashkey(self, payload: dict[str, Any]) -> str:
        if self.config.provider != "kis":
            return ""
        response = requests.post(
            f"{self.config.base_url}{self.config.hashkey_endpoint_path}",
            headers={
                "appkey": self.config.app_key or "",
                "appsecret": self.config.app_secret or "",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.config.timeout,
        )
        if not response.ok:
            raise RuntimeError(f"hashkey failed: HTTP {response.status_code} {response.text[:500]}")
        body = response.json()
        hashkey = body.get("HASH") or body.get("hash")
        if not hashkey:
            raise RuntimeError(f"hashkey response missing HASH: {body}")
        return str(hashkey)

    def _access_token(self) -> str:
        if self.config.access_token:
            return self.config.access_token
        if self.config.provider == "kis":
            response = requests.post(
                f"{self.config.base_url}/oauth2/tokenP",
                headers={"Content-Type": "application/json"},
                json={
                    "grant_type": "client_credentials",
                    "appkey": self.config.app_key,
                    "appsecret": self.config.app_secret,
                },
                timeout=self.config.timeout,
            )
        else:
            response = requests.post(
                f"{self.config.base_url}/oauth2/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                },
                timeout=self.config.timeout,
            )
        if not response.ok:
            raise RuntimeError(f"token failed: HTTP {response.status_code} {response.text[:500]}")
        token = response.json().get("access_token")
        if not token:
            raise RuntimeError("token response has no access_token")
        return token


def _quantity_text(value: float | None) -> str:
    if value is None:
        return ""
    if float(value).is_integer():
        return str(int(value))
    return str(value)


def _price_text(intent: OrderIntent) -> str:
    if intent.order_type.upper() == "LIMIT":
        return "" if intent.limit_price is None else str(int(intent.limit_price) if float(intent.limit_price).is_integer() else intent.limit_price)
    return "0"
