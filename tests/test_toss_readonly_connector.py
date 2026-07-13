import pytest

from toss_alpha.connectors.toss_readonly import TossReadOnlyClient


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text="ok"):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = text
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


def test_connector_builds_authorization_header(monkeypatch):
    calls = []

    def fake_post(url, headers=None, data=None, timeout=None):
        return FakeResponse(payload={"access_token": "token-123"})

    def fake_request(method, url, headers=None, params=None, timeout=None):
        calls.append({"method": method, "url": url, "headers": headers, "params": params})
        return FakeResponse(payload={"ok": True}, headers={"X-Request-Id": "req-1"})

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("requests.request", fake_request)

    client = TossReadOnlyClient(client_id="cid", client_secret="sec")
    result = client.prices("005930")

    assert calls[0]["headers"]["Authorization"] == "Bearer token-123"
    assert result["headers"]["X-Request-Id"] == "req-1"


def test_account_endpoints_require_account_seq(monkeypatch):
    monkeypatch.setattr("requests.post", lambda *a, **k: FakeResponse(payload={"access_token": "token"}))
    client = TossReadOnlyClient(client_id="cid", client_secret="sec")

    with pytest.raises(ValueError, match="account_seq"):
        client.accounts()


def test_connector_exposes_only_readonly_methods():
    client = TossReadOnlyClient(client_id="cid", client_secret="sec", account_seq="acc")
    for name in ["token", "stocks", "prices", "candles", "accounts", "holdings", "orders", "order_detail"]:
        assert callable(getattr(client, name))
    for forbidden in ["place_order", "cancel_order", "buy", "sell"]:
        assert not hasattr(client, forbidden)
