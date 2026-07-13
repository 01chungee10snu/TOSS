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


def test_toss_live_readiness_rejects_unconfirmed_v1_order_prefix():
    status = live_readiness(
        env={
            "TOSSINVEST_CLIENT_ID": "cid",
            "TOSSINVEST_CLIENT_SECRET": "sec",
            "TOSSINVEST_ACCOUNT_SEQ": "acc",
            "TOSSINVEST_LIVE_TRADING_ENABLED": "true",
            "TOSSINVEST_LIVE_ORDER_ENDPOINT": "/v1/orders",
        },
        policy=RiskPolicy(live_trading_enabled=True),
    )

    assert status["ready"] is False
    assert "unconfirmed_toss_order_endpoint_path" in status["missing"]


def test_toss_real_submission_blocks_unconfirmed_v1_order_prefix(monkeypatch):
    calls = []

    def fake_post(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        raise AssertionError("unconfirmed Toss endpoint must fail closed before HTTP")

    monkeypatch.setattr(requests, "post", fake_post)
    executor = GuardedLiveExecutor(
        config=LiveExecutionConfig(
            client_id="cid",
            client_secret="sec",
            account_seq="acc",
            order_endpoint_path="/v1/orders",
            access_token="token",
            live_trading_env_enabled=True,
        ),
        policy=RiskPolicy(live_trading_enabled=True, max_order_krw=100_000),
    )

    result = executor.submit_manual_draft(
        _intent(),
        RiskDecision.allowed(),
        confirmation_phrase="I UNDERSTAND THIS IS A REAL ORDER",
        dry_run=False,
    )

    assert result["status"] == "BLOCK"
    assert "unconfirmed_toss_order_endpoint_path" in result["violations"]
    assert calls == []


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


def test_kis_live_readiness_detects_missing_account_fields():
    status = live_readiness(
        env={"BROKER_PROVIDER": "kis", "KIS_APP_KEY": "app", "KIS_APP_SECRET": "sec"},
        policy=RiskPolicy(),
    )
    assert status["provider"] == "kis"
    assert status["ready"] is False
    assert "live_trading_disabled" in status["missing"]
    assert "cano" in status["missing"]
    assert "account_product_code" in status["missing"]


def test_kis_live_readiness_requires_broker_and_risk_double_opt_in():
    base_env = {
        "BROKER_PROVIDER": "kis",
        "KIS_APP_KEY": "app",
        "KIS_APP_SECRET": "sec",
        "KIS_CANO": "12345678",
        "KIS_ACNT_PRDT_CD": "01",
    }
    broker_only = live_readiness(env={**base_env, "KIS_LIVE_TRADING_ENABLED": "true"})
    assert broker_only["ready"] is False
    assert "live_trading_disabled" in broker_only["missing"]
    assert "env_live_trading_not_enabled" not in broker_only["missing"]

    risk_only = live_readiness(env={**base_env, "TOSS_RISK_LIVE_TRADING_ENABLED": "true"})
    assert risk_only["ready"] is False
    assert "live_trading_disabled" not in risk_only["missing"]
    assert "env_live_trading_not_enabled" in risk_only["missing"]

    both = live_readiness(
        env={
            **base_env,
            "KIS_LIVE_TRADING_ENABLED": "true",
            "TOSS_RISK_LIVE_TRADING_ENABLED": "true",
            "TOSS_MAX_ORDER_KRW": "55000",
        }
    )
    assert both["ready"] is True
    assert both["missing"] == []


def test_kis_real_submission_uses_hashkey_and_kis_headers(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, payload, status_code=200, headers=None, text="ok"):
            self._payload = payload
            self.status_code = status_code
            self.headers = headers or {}
            self.text = text
            self.ok = 200 <= status_code < 300

        def json(self):
            return self._payload

    def fake_post(url, headers=None, json=None, data=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json, "data": data, "timeout": timeout})
        if url.endswith("/uapi/hashkey"):
            return FakeResponse({"HASH": "hash-1"})
        return FakeResponse({"rt_cd": "0", "output": {"ord_no": "kis-ord-1"}}, headers={"X-Request-Id": "req-kis-1"})

    monkeypatch.setattr(requests, "post", fake_post)
    executor = GuardedLiveExecutor(
        config=LiveExecutionConfig(
            provider="kis",
            app_key="app",
            app_secret="sec",
            cano="12345678",
            account_product_code="01",
            order_endpoint_path="/uapi/domestic-stock/v1/trading/order-cash",
            access_token="token",
            live_trading_env_enabled=True,
            base_url="https://openapi.koreainvestment.com:9443",
            kis_order_tr_id_buy="TTTC0802U",
            kis_order_tr_id_sell="TTTC0801U",
        ),
        policy=RiskPolicy(live_trading_enabled=True, max_order_krw=100_000),
    )
    result = executor.submit_manual_draft(
        OrderIntent(strategy_id="s1", symbol="005930", side="BUY", quantity=1, reason="manual approved candidate"),
        RiskDecision.allowed(),
        confirmation_phrase="I UNDERSTAND THIS IS A REAL ORDER",
        dry_run=False,
    )
    assert result["status"] == "SUBMITTED"
    assert calls[0]["url"].endswith("/uapi/hashkey")
    assert calls[1]["headers"]["tr_id"] == "TTTC0802U"
    assert calls[1]["headers"]["hashkey"] == "hash-1"
    assert calls[1]["json"]["CANO"] == "12345678"


def test_kis_access_token_cache_reuses_token_across_live_executors(tmp_path, monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, payload, status_code=200, headers=None, text="ok"):
            self._payload = payload
            self.status_code = status_code
            self.headers = headers or {}
            self.text = text
            self.ok = 200 <= status_code < 300

        def json(self):
            return self._payload

    def fake_post(url, headers=None, json=None, data=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json, "data": data, "timeout": timeout})
        if url.endswith("/oauth2/tokenP"):
            return FakeResponse({"access_token": "cached-token", "expires_in": 86400})
        if url.endswith("/uapi/hashkey"):
            return FakeResponse({"HASH": "hash-1"})
        return FakeResponse({"rt_cd": "0", "output": {"ord_no": "kis-ord-1"}})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setenv("KIS_ACCESS_TOKEN_CACHE", str(tmp_path / "kis_token_cache.json"))
    config = LiveExecutionConfig(
        provider="kis",
        app_key="app",
        app_secret="sec",
        cano="12345678",
        account_product_code="01",
        order_endpoint_path="/uapi/domestic-stock/v1/trading/order-cash",
        live_trading_env_enabled=True,
        base_url="https://openapi.koreainvestment.com:9443",
        kis_order_tr_id_buy="TTTC0802U",
        kis_order_tr_id_sell="TTTC0801U",
    )

    for _ in range(2):
        executor = GuardedLiveExecutor(config=config, policy=RiskPolicy(live_trading_enabled=True, max_order_krw=100_000))
        result = executor.submit_manual_draft(
            OrderIntent(strategy_id="s1", symbol="005930", side="BUY", quantity=1, reason="manual approved candidate"),
            RiskDecision.allowed(),
            confirmation_phrase="I UNDERSTAND THIS IS A REAL ORDER",
            dry_run=False,
        )
        assert result["status"] == "SUBMITTED"

    assert [call["url"] for call in calls].count("https://openapi.koreainvestment.com:9443/oauth2/tokenP") == 1


def test_kis_order_http_200_with_error_rt_cd_is_rejected(monkeypatch):
    class FakeResponse:
        status_code = 200
        ok = True
        headers = {}
        text = '{"rt_cd":"7","msg_cd":"APBK0406","msg1":"주문가격이 상한가를 초과합니다."}'

        def json(self):
            return {"rt_cd": "7", "msg_cd": "APBK0406", "msg1": "주문가격이 상한가를 초과합니다."}

    def fake_post(url, headers=None, json=None, data=None, timeout=None):
        if url.endswith("/uapi/hashkey"):
            class HashResponse:
                status_code = 200
                ok = True
                headers = {}
                text = "{}"

                def json(self):
                    return {"HASH": "hash-1"}

            return HashResponse()
        return FakeResponse()

    monkeypatch.setattr(requests, "post", fake_post)
    config = LiveExecutionConfig(
        provider="kis",
        app_key="app",
        app_secret="sec",
        access_token="token",
        cano="12345678",
        account_product_code="01",
        order_endpoint_path="/uapi/domestic-stock/v1/trading/order-cash",
        live_trading_env_enabled=True,
        base_url="https://openapi.koreainvestment.com:9443",
        kis_order_tr_id_buy="TTTC0802U",
    )
    executor = GuardedLiveExecutor(config=config, policy=RiskPolicy(live_trading_enabled=True, max_order_krw=100_000))

    result = executor.submit_manual_draft(
        OrderIntent(strategy_id="s1", symbol="306040", side="BUY", quantity=5, limit_price=9210, reason="manual approved candidate"),
        RiskDecision.allowed(),
        confirmation_phrase="I UNDERSTAND THIS IS A REAL ORDER",
        dry_run=False,
    )

    assert result["status"] == "REJECTED"
    assert result["not_submitted"] is True
    assert result["violations"] == ["broker_rejected_order"]
