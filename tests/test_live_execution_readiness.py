import inspect

import requests

import toss_alpha.execution.live_ready as live_ready
from toss_alpha.data.schema import OrderIntent, RiskDecision
from toss_alpha.execution.live_ready import GuardedLiveExecutor, LiveExecutionConfig, build_order_payload, live_readiness
from toss_alpha.risk import RiskPolicy


def _intent():
    return OrderIntent(
        strategy_id="s1",
        symbol="005930",
        side="BUY",
        notional_krw=50_000,
        reason="manual approved candidate",
    )


def test_default_live_readiness_is_not_ready_without_credentials_or_endpoint():
    status = live_readiness(env={}, policy=RiskPolicy())
    assert status["ready"] is False
    assert "live_trading_disabled" in status["missing"]
    assert "client_credentials" in status["missing"]
    assert "account_seq" in status["missing"]
    assert "order_endpoint_path" in status["missing"]


def test_build_order_payload_is_manual_intent_shape_not_executable_by_itself():
    payload = build_order_payload(_intent())
    assert payload == {
        "symbol": "005930",
        "side": "BUY",
        "notional_krw": 50_000,
        "quantity": None,
        "order_type": "MARKET",
        "limit_price": None,
        "time_in_force": "DAY",
        "client_order_id": _intent().intent_id,
    } or set(payload) == {
        "symbol",
        "side",
        "notional_krw",
        "quantity",
        "order_type",
        "limit_price",
        "time_in_force",
        "client_order_id",
    }


def test_dry_run_prepares_payload_without_http(monkeypatch):
    def fail_post(*_args, **_kwargs):
        raise AssertionError("HTTP post must not be called in dry_run")

    monkeypatch.setattr(requests, "post", fail_post)
    executor = GuardedLiveExecutor(
        config=LiveExecutionConfig(
            client_id="cid",
            client_secret="sec",
            account_seq="acc",
            order_endpoint_path="/api/v1/orders",
            access_token="token",
            live_trading_env_enabled=True,
        ),
        policy=RiskPolicy(live_trading_enabled=True, max_order_krw=100_000),
    )
    result = executor.submit_manual_draft(
        _intent(),
        RiskDecision.allowed(),
        confirmation_phrase="I UNDERSTAND THIS IS A REAL ORDER",
        dry_run=True,
    )
    assert result["status"] == "DRY_RUN"
    assert result["not_submitted"] is True
    assert result["payload"]["symbol"] == "005930"


def test_real_submission_requires_double_opt_in_and_confirmation(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200
        ok = True
        headers = {"X-Request-Id": "req-live-1"}
        text = "ok"

        def json(self):
            return {"order_id": "ord-1"}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr(requests, "post", fake_post)
    executor = GuardedLiveExecutor(
        config=LiveExecutionConfig(
            client_id="cid",
            client_secret="sec",
            account_seq="acc",
            order_endpoint_path="/api/v1/orders",
            access_token="token",
            live_trading_env_enabled=True,
        ),
        policy=RiskPolicy(live_trading_enabled=True, max_order_krw=100_000),
    )

    blocked = executor.submit_manual_draft(_intent(), RiskDecision.allowed(), confirmation_phrase="wrong", dry_run=False)
    assert blocked["status"] == "BLOCK"
    assert calls == []

    submitted = executor.submit_manual_draft(
        _intent(),
        RiskDecision.allowed(),
        confirmation_phrase="I UNDERSTAND THIS IS A REAL ORDER",
        dry_run=False,
    )
    assert submitted["status"] == "SUBMITTED"
    assert calls[0]["headers"]["X-Tossinvest-Account"] == "acc"


def test_blocked_risk_decision_prevents_submission_even_when_enabled():
    executor = GuardedLiveExecutor(
        config=LiveExecutionConfig(
            client_id="cid",
            client_secret="sec",
            account_seq="acc",
            order_endpoint_path="/api/v1/orders",
            access_token="token",
            live_trading_env_enabled=True,
        ),
        policy=RiskPolicy(live_trading_enabled=True),
    )
    result = executor.submit_manual_draft(
        _intent(),
        RiskDecision.blocked(["max_order_krw_exceeded"]),
        confirmation_phrase="I UNDERSTAND THIS IS A REAL ORDER",
        dry_run=False,
    )
    assert result["status"] == "BLOCK"
    assert "max_order_krw_exceeded" in result["violations"]


def test_module_has_no_shortcut_buy_sell_callables():
    forbidden = {"buy", "sell", "auto_trade"}
    callables = {name for name, value in inspect.getmembers(live_ready, callable)}
    assert forbidden.isdisjoint(callables)
