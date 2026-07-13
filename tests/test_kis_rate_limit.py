from __future__ import annotations

import json
from pathlib import Path

import requests

from toss_alpha.connectors.kis_rate_limit import kis_post, request_with_kis_safety
from toss_alpha.connectors.kis_token_cache import cached_kis_access_token


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text="ok"):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.text = text
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


def test_kis_rate_limit_retries_known_kis_egw00215_without_real_sleep(tmp_path, monkeypatch):
    calls = []
    sleeps = []
    monkeypatch.setenv("KIS_RATE_LIMIT_STATE_PATH", str(tmp_path / "rate_state.json"))
    monkeypatch.setenv("KIS_RATE_LIMIT_AUDIT_PATH", str(tmp_path / "rate_audit.jsonl"))
    monkeypatch.setenv("KIS_RATE_MIN_INTERVAL_SEC", "0")
    monkeypatch.setenv("KIS_RATE_RETRY_BASE_DELAY_SEC", "0.01")
    monkeypatch.setenv("KIS_RATE_MAX_RETRIES", "2")
    monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(seconds))

    def fake_post(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        if len(calls) == 1:
            return FakeResponse(
                status_code=500,
                payload={"msg_cd": "EGW00215", "msg1": "원장에서 허용 가능한 초당 거래건수를 초과하였습니다."},
                text='{"msg_cd":"EGW00215"}',
            )
        return FakeResponse(payload={"rt_cd": "0"})

    monkeypatch.setattr(requests, "post", fake_post)

    response = kis_post("https://openapi.koreainvestment.com:9443/uapi/hashkey", json={"x": 1})

    assert response.ok is True
    assert len(calls) == 2
    assert sleeps and sleeps[0] >= 0.01
    audit_lines = (tmp_path / "rate_audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert any("kis_rate_limited" in line for line in audit_lines)


def test_kis_rate_limit_retries_known_kis_egw00133_token_frequency(tmp_path, monkeypatch):
    calls = []
    sleeps = []
    monkeypatch.setenv("KIS_RATE_LIMIT_STATE_PATH", str(tmp_path / "rate_state.json"))
    monkeypatch.setenv("KIS_RATE_LIMIT_AUDIT_PATH", str(tmp_path / "rate_audit.jsonl"))
    monkeypatch.setenv("KIS_RATE_MIN_INTERVAL_SEC", "0")
    monkeypatch.setenv("KIS_RATE_RETRY_BASE_DELAY_SEC", "0.01")
    monkeypatch.setenv("KIS_RATE_MAX_RETRIES", "2")
    monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(seconds))

    def fake_post(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        if len(calls) == 1:
            return FakeResponse(
                status_code=200,
                payload={"msg_cd": "EGW00133", "msg1": "접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)"},
                text='{"msg_cd":"EGW00133"}',
            )
        return FakeResponse(payload={"access_token": "ok", "expires_in": 86400})

    monkeypatch.setattr(requests, "post", fake_post)

    response = kis_post("https://openapi.koreainvestment.com:9443/oauth2/tokenP", json={"grant_type": "client_credentials"})

    assert response.ok is True
    assert response.json()["access_token"] == "ok"
    assert len(calls) == 2
    assert sleeps and sleeps[0] >= 0.01
    audit_lines = (tmp_path / "rate_audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert any('"event": "kis_rate_limited"' in line for line in audit_lines)


def test_kis_request_pacing_writes_shared_state_without_sleep_when_interval_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("KIS_RATE_LIMIT_STATE_PATH", str(tmp_path / "rate_state.json"))
    monkeypatch.setenv("KIS_RATE_LIMIT_AUDIT_PATH", "off")
    monkeypatch.setenv("KIS_RATE_MIN_INTERVAL_SEC", "0")

    response = request_with_kis_safety(
        lambda: FakeResponse(payload={"output": {}}),
        method="GET",
        url="https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-price",
    )

    assert response.ok is True
    state = json.loads((tmp_path / "rate_state.json").read_text(encoding="utf-8"))
    assert state["last_method"] == "GET"
    assert "last_call_epoch" in state
    assert "last_call_monotonic" not in state
    assert "inquire-price" in state["last_url_path"]


def test_kis_token_cache_lock_rechecks_cache_before_fetching_again(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("KIS_RATE_LIMIT_ENABLED", "false")
    cache_path = tmp_path / "kis_token_cache.json"

    def fetch_token():
        calls.append("fetch")
        return {"access_token": "token-1", "expires_in": 86400}

    first = cached_kis_access_token(app_key="app", base_url="https://openapi.koreainvestment.com:9443", fetch_token=fetch_token, cache_path=cache_path)
    second = cached_kis_access_token(app_key="app", base_url="https://openapi.koreainvestment.com:9443", fetch_token=fetch_token, cache_path=cache_path)

    assert first == "token-1"
    assert second == "token-1"
    assert calls == ["fetch"]
    assert Path(str(cache_path) + ".lock").exists()


def test_kis_token_cache_paces_refetch_after_recent_failed_or_expired_token(tmp_path, monkeypatch):
    calls = []
    sleeps = []
    cache_path = tmp_path / "kis_token_cache.json"

    monkeypatch.setenv("KIS_RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("KIS_TOKEN_MIN_ISSUE_INTERVAL_SEC", "10")
    monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(seconds))

    def fetch_token():
        calls.append("fetch")
        return {"access_token": f"token-{len(calls)}", "expires_in": 1}

    first = cached_kis_access_token(app_key="app", base_url="https://openapi.koreainvestment.com:9443", fetch_token=fetch_token, cache_path=cache_path)
    assert first == "token-1"
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    entry = next(iter(cache["tokens"].values()))
    entry["expires_at"] = "2000-01-01T00:00:00+00:00"
    entry["last_token_fetch_epoch"] = __import__("time").time()
    cache_path.write_text(json.dumps(cache), encoding="utf-8")

    second = cached_kis_access_token(app_key="app", base_url="https://openapi.koreainvestment.com:9443", fetch_token=fetch_token, cache_path=cache_path)

    assert second == "token-2"
    assert len(calls) == 2
    assert sleeps and sleeps[0] > 0


def test_kis_request_token_frequency_retry_uses_token_specific_delay(tmp_path, monkeypatch):
    calls = []
    sleeps = []
    monkeypatch.setenv("KIS_RATE_LIMIT_STATE_PATH", str(tmp_path / "rate_state.json"))
    monkeypatch.setenv("KIS_RATE_LIMIT_AUDIT_PATH", "off")
    monkeypatch.setenv("KIS_RATE_MIN_INTERVAL_SEC", "0")
    monkeypatch.setenv("KIS_RATE_MAX_RETRIES", "1")
    monkeypatch.setenv("KIS_TOKEN_RETRY_DELAY_SEC", "61")
    monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(seconds))

    def fake_post(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return FakeResponse(status_code=403, payload={"error_code": "EGW00133", "error_description": "접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)"}, text='{"error_code":"EGW00133"}')
        return FakeResponse(payload={"access_token": "ok", "expires_in": 86400})

    monkeypatch.setattr(requests, "post", fake_post)

    response = kis_post("https://openapi.koreainvestment.com:9443/oauth2/tokenP", json={"grant_type": "client_credentials"})

    assert response.ok is True
    assert sleeps == [61.0]


def test_kis_request_pacing_ignores_stale_future_state_without_huge_sleep(tmp_path, monkeypatch):
    state_path = tmp_path / "rate_state.json"
    state_path.write_text(json.dumps({"last_call_epoch": 999999999999.0}), encoding="utf-8")
    sleeps = []
    monkeypatch.setenv("KIS_RATE_LIMIT_STATE_PATH", str(state_path))
    monkeypatch.setenv("KIS_RATE_LIMIT_AUDIT_PATH", "off")
    monkeypatch.setenv("KIS_RATE_MIN_INTERVAL_SEC", "0.7")
    monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(seconds))

    response = request_with_kis_safety(
        lambda: FakeResponse(payload={"output": {}}),
        method="GET",
        url="https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-price",
    )

    assert response.ok is True
    assert sleeps == []
    repaired = json.loads(state_path.read_text(encoding="utf-8"))
    assert repaired["last_call_epoch"] < 999999999999.0
