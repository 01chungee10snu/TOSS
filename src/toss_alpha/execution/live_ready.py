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

BASE_URL = "https://openapi.tossinvest.com"
REAL_ORDER_CONFIRMATION_PHRASE = "I UNDERSTAND THIS IS A REAL ORDER"


@dataclass(frozen=True)
class LiveExecutionConfig:
    client_id: str | None = None
    client_secret: str | None = None
    account_seq: str | None = None
    order_endpoint_path: str | None = None
    access_token: str | None = None
    live_trading_env_enabled: bool = False
    base_url: str = BASE_URL
    timeout: int = 20
    confirmation_phrase: str = REAL_ORDER_CONFIRMATION_PHRASE

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LiveExecutionConfig":
        source = os.environ if env is None else env
        return cls(
            client_id=_blank_to_none(source.get("TOSSINVEST_CLIENT_ID")),
            client_secret=_blank_to_none(source.get("TOSSINVEST_CLIENT_SECRET")),
            account_seq=_blank_to_none(source.get("TOSSINVEST_ACCOUNT_SEQ")),
            order_endpoint_path=_blank_to_none(source.get("TOSSINVEST_LIVE_ORDER_ENDPOINT")),
            access_token=_blank_to_none(source.get("TOSSINVEST_ACCESS_TOKEN")),
            live_trading_env_enabled=source.get("TOSSINVEST_LIVE_TRADING_ENABLED", "").lower() == "true",
            confirmation_phrase=source.get("TOSSINVEST_REAL_ORDER_CONFIRMATION", REAL_ORDER_CONFIRMATION_PHRASE),
        )


def _blank_to_none(value: str | None) -> str | None:
    if value is None or value.strip() == "":
        return None
    return value.strip()


def live_readiness(env: Mapping[str, str] | None = None, policy: RiskPolicy | None = None) -> dict[str, Any]:
    config = LiveExecutionConfig.from_env(env)
    policy = policy or RiskPolicy()
    missing: list[str] = []
    if not policy.live_trading_enabled:
        missing.append("live_trading_disabled")
    if not config.live_trading_env_enabled:
        missing.append("env_live_trading_not_enabled")
    if not (config.client_id and config.client_secret):
        missing.append("client_credentials")
    if not config.account_seq:
        missing.append("account_seq")
    if not config.order_endpoint_path:
        missing.append("order_endpoint_path")
    return {
        "ready": not missing,
        "missing": missing,
        "dry_run_available": bool(config.client_id and config.client_secret and config.account_seq),
        "requires_confirmation_phrase": config.confirmation_phrase,
        "default_mode": "BLOCK_UNLESS_DOUBLE_OPT_IN",
    }


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
        payload = build_order_payload(intent)
        violations = self._blocking_violations(risk_decision, confirmation_phrase=confirmation_phrase)
        if dry_run:
            return {
                "status": "DRY_RUN" if not risk_decision.violations else "BLOCK",
                "not_submitted": True,
                "payload": payload,
                "violations": violations,
                "mode": "manual_approved_live_ready_dry_run",
            }
        if violations:
            return {"status": "BLOCK", "not_submitted": True, "payload": payload, "violations": violations}

        response = requests.post(
            f"{self.config.base_url}{self.config.order_endpoint_path}",
            headers={
                "Authorization": f"Bearer {self._access_token()}",
                "X-Tossinvest-Account": self.config.account_seq or "",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.config.timeout,
        )
        try:
            body = response.json()
        except Exception:
            body = None
        result = {
            "status": "SUBMITTED" if response.ok else "REJECTED",
            "status_code": response.status_code,
            "headers": {"X-Request-Id": response.headers.get("X-Request-Id")} if response.headers.get("X-Request-Id") else {},
            "json": body,
            "text": response.text,
        }
        if not response.ok:
            result["violations"] = ["broker_rejected_order"]
        return result

    def _blocking_violations(self, risk_decision: RiskDecision, *, confirmation_phrase: str) -> list[str]:
        violations = list(risk_decision.violations)
        if not risk_decision.allow:
            violations.append("risk_decision_blocked")
        if not self.policy.live_trading_enabled:
            violations.append("live_trading_disabled")
        if not self.config.live_trading_env_enabled:
            violations.append("env_live_trading_not_enabled")
        if not self.config.order_endpoint_path:
            violations.append("order_endpoint_path_missing")
        if not self.config.account_seq:
            violations.append("account_seq_missing")
        if confirmation_phrase != self.config.confirmation_phrase:
            violations.append("real_order_confirmation_phrase_mismatch")
        return list(dict.fromkeys(violations))

    def _access_token(self) -> str:
        if self.config.access_token:
            return self.config.access_token
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
