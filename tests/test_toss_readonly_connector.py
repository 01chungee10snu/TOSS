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


def _setup_monkeypatch(monkeypatch):
    """Wire fake token + request capture."""
    calls = []

    def fake_post(url, headers=None, data=None, timeout=None):
        return FakeResponse(payload={"access_token": "token-123"})

    def fake_request(method, url, headers=None, params=None, timeout=None):
        calls.append({"method": method, "url": url, "headers": headers, "params": params})
        return FakeResponse(payload={"ok": True}, headers={"X-Request-Id": "req-1"})

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("requests.request", fake_request)
    return calls


# ── Original tests ────────────────────────────────────────

def test_connector_builds_authorization_header(monkeypatch):
    calls = _setup_monkeypatch(monkeypatch)

    client = TossReadOnlyClient(client_id="cid", client_secret="sec")
    result = client.prices("005930")

    assert calls[0]["headers"]["Authorization"] == "Bearer token-123"
    assert result["headers"]["X-Request-Id"] == "req-1"


def test_account_endpoints_require_account_seq(monkeypatch):
    _setup_monkeypatch(monkeypatch)
    client = TossReadOnlyClient(client_id="cid", client_secret="sec")

    with pytest.raises(ValueError, match="account_seq"):
        client.accounts()


def test_connector_exposes_only_readonly_methods():
    client = TossReadOnlyClient(client_id="cid", client_secret="sec", account_seq="acc")
    for name in [
        "token", "stocks", "prices", "candles", "orderbook", "trades",
        "price_limits", "warnings", "exchange_rate", "market_calendar_kr",
        "market_calendar_us", "accounts", "holdings", "buying_power",
        "sellable_quantity", "commissions", "orders", "order_detail",
    ]:
        assert callable(getattr(client, name)), f"missing: {name}"
    for forbidden in ["place_order", "buy", "sell", "modify_order", "cancel_order"]:
        assert not hasattr(client, forbidden), f"forbidden method exists: {forbidden}"


# ── Market Data ───────────────────────────────────────────

def test_orderbook(monkeypatch):
    calls = _setup_monkeypatch(monkeypatch)
    client = TossReadOnlyClient(client_id="cid", client_secret="sec")
    client.orderbook("005930,373220")
    assert calls[0]["params"] == {"symbols": "005930,373220"}
    assert "/api/v1/orderbook" in calls[0]["url"]


def test_trades(monkeypatch):
    calls = _setup_monkeypatch(monkeypatch)
    client = TossReadOnlyClient(client_id="cid", client_secret="sec")
    client.trades("005930")
    assert "/api/v1/trades" in calls[0]["url"]


def test_price_limits(monkeypatch):
    calls = _setup_monkeypatch(monkeypatch)
    client = TossReadOnlyClient(client_id="cid", client_secret="sec")
    client.price_limits("005930")
    assert "/api/v1/price-limits" in calls[0]["url"]


# ── Stock Info ────────────────────────────────────────────

def test_warnings(monkeypatch):
    calls = _setup_monkeypatch(monkeypatch)
    client = TossReadOnlyClient(client_id="cid", client_secret="sec")
    client.warnings("005930")
    assert calls[0]["url"].endswith("/api/v1/stocks/005930/warnings")


# ── Market Info ───────────────────────────────────────────

def test_exchange_rate(monkeypatch):
    calls = _setup_monkeypatch(monkeypatch)
    client = TossReadOnlyClient(client_id="cid", client_secret="sec")
    client.exchange_rate()
    assert calls[0]["url"].endswith("/api/v1/exchange-rate")


def test_market_calendar_kr(monkeypatch):
    calls = _setup_monkeypatch(monkeypatch)
    client = TossReadOnlyClient(client_id="cid", client_secret="sec")
    client.market_calendar_kr()
    assert calls[0]["url"].endswith("/api/v1/market-calendar/KR")


def test_market_calendar_us(monkeypatch):
    calls = _setup_monkeypatch(monkeypatch)
    client = TossReadOnlyClient(client_id="cid", client_secret="sec")
    client.market_calendar_us()
    assert calls[0]["url"].endswith("/api/v1/market-calendar/US")


# ── Order Info (read-only, needs account) ─────────────────

def test_buying_power_requires_account(monkeypatch):
    _setup_monkeypatch(monkeypatch)
    client = TossReadOnlyClient(client_id="cid", client_secret="sec")
    with pytest.raises(ValueError, match="account_seq"):
        client.buying_power()


def test_buying_power_with_account(monkeypatch):
    calls = _setup_monkeypatch(monkeypatch)
    client = TossReadOnlyClient(client_id="cid", client_secret="sec", account_seq="1")
    client.buying_power()
    assert "/api/v1/buying-power" in calls[0]["url"]
    assert calls[0]["headers"]["X-Tossinvest-Account"] == "1"


def test_sellable_quantity(monkeypatch):
    calls = _setup_monkeypatch(monkeypatch)
    client = TossReadOnlyClient(client_id="cid", client_secret="sec", account_seq="1")
    client.sellable_quantity("005930")
    assert calls[0]["params"] == {"symbol": "005930"}
    assert calls[0]["headers"]["X-Tossinvest-Account"] == "1"


def test_commissions(monkeypatch):
    calls = _setup_monkeypatch(monkeypatch)
    client = TossReadOnlyClient(client_id="cid", client_secret="sec", account_seq="1")
    client.commissions()
    assert "/api/v1/commissions" in calls[0]["url"]


# ── Order History (read-only, needs account) ──────────────

def test_orders_list(monkeypatch):
    calls = _setup_monkeypatch(monkeypatch)
    client = TossReadOnlyClient(client_id="cid", client_secret="sec", account_seq="1")
    client.orders()
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].rstrip("/").endswith("/api/v1/orders")
    assert calls[0]["params"] is None


def test_orders_with_status_filter(monkeypatch):
    calls = _setup_monkeypatch(monkeypatch)
    client = TossReadOnlyClient(client_id="cid", client_secret="sec", account_seq="1")
    client.orders(status="WAITING")
    assert calls[0]["params"] == {"status": "WAITING"}


def test_order_detail(monkeypatch):
    calls = _setup_monkeypatch(monkeypatch)
    client = TossReadOnlyClient(client_id="cid", client_secret="sec", account_seq="1")
    client.order_detail("order-abc")
    assert calls[0]["url"].endswith("/api/v1/orders/order-abc")


def test_orders_require_account(monkeypatch):
    _setup_monkeypatch(monkeypatch)
    client = TossReadOnlyClient(client_id="cid", client_secret="sec")
    with pytest.raises(ValueError, match="account_seq"):
        client.orders()


# ── Error handling ────────────────────────────────────────

def test_request_failure_raises(monkeypatch):
    def fake_post(url, headers=None, data=None, timeout=None):
        return FakeResponse(payload={"access_token": "token-123"})

    def fake_request(method, url, headers=None, params=None, timeout=None):
        return FakeResponse(status_code=429, payload={"error": "rate limited"}, text="429")

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("requests.request", fake_request)

    client = TossReadOnlyClient(client_id="cid", client_secret="sec")
    with pytest.raises(RuntimeError, match="429"):
        client.stocks("005930")
