import pytest

from toss_alpha.connectors.kis_readonly import KisReadOnlyClient


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text="ok"):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = text
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


def test_kis_connector_builds_authorization_and_app_headers(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        return FakeResponse(payload={"access_token": "kis-token"})

    def fake_request(method, url, headers=None, params=None, timeout=None):
        calls.append({"method": method, "url": url, "headers": headers, "params": params})
        return FakeResponse(payload={"output1": [], "output2": [{"dnca_tot_amt": "1000000"}]}, headers={"tr_id": headers.get("tr_id", "")})

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("requests.request", fake_request)

    client = KisReadOnlyClient(app_key="app", app_secret="sec", cano="12345678", account_product_code="01")
    result = client.balance()

    assert calls[0]["headers"]["Authorization"] == "Bearer kis-token"
    assert calls[0]["headers"]["appkey"] == "app"
    assert calls[0]["params"]["CANO"] == "12345678"
    assert calls[0]["params"]["ACNT_PRDT_CD"] == "01"
    assert result["headers"]["tr_id"]


def test_kis_connector_exposes_only_readonly_methods():
    client = KisReadOnlyClient(app_key="app", app_secret="sec", cano="12345678")
    for name in ["token", "balance", "account_snapshot", "position_snapshots"]:
        assert callable(getattr(client, name))
    for forbidden in ["orders", "place_order", "buy", "sell"]:
        assert not hasattr(client, forbidden)


def test_kis_account_snapshot_and_positions_parse_outputs(monkeypatch):
    payload = {
        "output1": [
            {
                "pdno": "005930",
                "hldg_qty": "3",
                "ord_psbl_qty": "2",
                "pchs_avg_pric": "70000",
                "evlu_amt": "210000",
                "evlu_pfls_amt": "5000",
            }
        ],
        "output2": [
            {
                "dnca_tot_amt": "1000000",
                "ord_psbl_cash": "900000",
                "tot_evlu_amt": "1210000",
            }
        ],
    }

    monkeypatch.setattr(KisReadOnlyClient, "balance", lambda self, query=None: {"json": payload})
    client = KisReadOnlyClient(app_key="app", app_secret="sec", cano="12345678")

    account = client.account_snapshot()
    positions = client.position_snapshots()

    assert account.source == "kis"
    assert account.cash == 1_000_000
    assert positions[0].symbol == "005930"
    assert positions[0].quantity == 3.0
    assert positions[0].sellable_quantity == 2.0
