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
    assert calls[0]["params"] == {
        "CANO": "12345678",
        "ACNT_PRDT_CD": "01",
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
    assert result["headers"]["tr_id"]


def test_kis_connector_exposes_only_readonly_methods():
    client = KisReadOnlyClient(app_key="app", app_secret="sec", cano="12345678")
    for name in ["token", "balance", "balance_all", "account_snapshot", "position_snapshots", "quote", "orderbook", "quote_snapshot"]:
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

    monkeypatch.setattr(KisReadOnlyClient, "balance", lambda self, **kwargs: {"json": payload, "headers": {}})
    client = KisReadOnlyClient(app_key="app", app_secret="sec", cano="12345678")

    account = client.account_snapshot()
    positions = client.position_snapshots()

    assert account.source == "kis"
    assert account.cash == 1_000_000
    assert positions[0].symbol == "005930"
    assert positions[0].quantity == 3.0
    assert positions[0].sellable_quantity == 2.0


def test_kis_balance_all_follows_official_continuation_contract(monkeypatch):
    calls = []
    pages = [
        {
            "json": {
                "output1": [{"pdno": "005930"}],
                "output2": [{"tot_evlu_amt": "100"}],
                "ctx_area_fk100": "next-fk",
                "ctx_area_nk100": "next-nk",
            },
            "headers": {"tr_cont": "M"},
        },
        {
            "json": {
                "output1": [{"pdno": "000660"}],
                "output2": [{"tot_evlu_amt": "200"}],
            },
            "headers": {"tr_cont": ""},
        },
    ]

    def fake_balance(self, *, query=None, tr_cont=""):
        calls.append({"query": query, "tr_cont": tr_cont})
        return pages[len(calls) - 1]

    monkeypatch.setattr(KisReadOnlyClient, "balance", fake_balance)
    client = KisReadOnlyClient(app_key="app", app_secret="sec", cano="12345678")

    result = client.balance_all()

    assert [row["pdno"] for row in result["json"]["output1"]] == ["005930", "000660"]
    assert result["json"]["output2"] == [{"tot_evlu_amt": "200"}]
    assert calls == [
        {"query": {}, "tr_cont": ""},
        {
            "query": {"CTX_AREA_FK100": "next-fk", "CTX_AREA_NK100": "next-nk"},
            "tr_cont": "N",
        },
    ]


def test_kis_quote_uses_readonly_current_price_endpoint(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        return FakeResponse(payload={"access_token": "kis-token"})

    def fake_request(method, url, headers=None, params=None, timeout=None):
        calls.append({"method": method, "url": url, "headers": headers, "params": params})
        return FakeResponse(payload={"output": {"stck_prpr": "70100", "bidp": "70000", "askp": "70200", "acml_vol": "12345"}})

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("requests.request", fake_request)

    client = KisReadOnlyClient(app_key="app", app_secret="sec", cano="12345678")
    result = client.quote("5930")

    assert result["ok"] is True
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/uapi/domestic-stock/v1/quotations/inquire-price")
    assert calls[0]["headers"]["tr_id"] == "FHKST01010100"
    assert calls[0]["params"] == {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}


def test_kis_quote_snapshot_parses_price_and_orderbook_payloads(monkeypatch):
    monkeypatch.setattr(
        KisReadOnlyClient,
        "quote",
        lambda self, symbol: {"json": {"output": {"stck_prpr": "70100", "acml_vol": "12345"}}},
    )
    monkeypatch.setattr(
        KisReadOnlyClient,
        "orderbook",
        lambda self, symbol: {"json": {"output1": {"bidp1": "70000", "askp1": "70200"}}},
    )
    client = KisReadOnlyClient(app_key="app", app_secret="sec", cano="12345678")

    quote = client.quote_snapshot("5930")

    assert quote.symbol == "005930"
    assert quote.last == 70100.0
    assert quote.bid == 70000.0
    assert quote.ask == 70200.0
    assert quote.volume == 12345.0
    assert quote.source == "kis"


def test_kis_orderbook_uses_readonly_orderbook_endpoint(monkeypatch):
    calls = []
    monkeypatch.setattr(KisReadOnlyClient, "token", lambda self: "token")

    def fake_request(method, url, headers=None, params=None, timeout=None):
        calls.append({"method": method, "url": url, "headers": headers, "params": params})
        return FakeResponse(payload={"output1": {"bidp1": "70000", "askp1": "70200"}})

    monkeypatch.setattr("requests.request", fake_request)
    client = KisReadOnlyClient(app_key="app", app_secret="sec", cano="12345678")

    result = client.orderbook("5930")

    assert result["ok"] is True
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].endswith("/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn")
    assert calls[0]["headers"]["tr_id"] == "FHKST01010200"
    assert calls[0]["params"] == {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}


def test_kis_quote_snapshot_keeps_missing_orderbook_fail_closed(monkeypatch):
    monkeypatch.setattr(
        KisReadOnlyClient,
        "quote",
        lambda self, symbol: {"json": {"output": {"stck_prpr": "70100", "acml_vol": "12345"}}},
    )
    monkeypatch.setattr(
        KisReadOnlyClient,
        "orderbook",
        lambda self, symbol: {"json": {"output1": {}}},
    )
    client = KisReadOnlyClient(app_key="app", app_secret="sec", cano="12345678")

    quote = client.quote_snapshot("5930")

    assert quote.last == 70100.0
    assert quote.bid is None
    assert quote.ask is None


def test_kis_business_error_under_http_200_raises(monkeypatch):
    monkeypatch.setattr(KisReadOnlyClient, "token", lambda self: "token")
    monkeypatch.setattr(
        "requests.request",
        lambda *args, **kwargs: FakeResponse(
            payload={"rt_cd": "1", "msg_cd": "EGW00133", "msg1": "expired token"}
        ),
    )
    client = KisReadOnlyClient(app_key="app", app_secret="sec", cano="12345678")

    with pytest.raises(RuntimeError, match="KIS EGW00133"):
        client.balance()
