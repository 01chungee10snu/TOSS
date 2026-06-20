import pytest

from toss_alpha.connectors.toss_readonly import TossReadOnlyClient
from toss_alpha.data.schema import OrderIntent, PositionSnapshot
from toss_alpha.execution.ledger import ExecutionLedger
from toss_alpha.execution.paper_executor import PaperExecutor
from toss_alpha.execution.reconcile import reconcile_account_state


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text="ok"):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = text
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


def test_connector_exposes_normalized_account_and_holdings_snapshots(monkeypatch):
    def fake_post(url, headers=None, data=None, timeout=None):
        return FakeResponse(payload={"access_token": "token-123"})

    def fake_request(method, url, headers=None, params=None, timeout=None):
        if url.endswith("/api/v1/accounts"):
            return FakeResponse(
                payload={
                    "result": {
                        "accountSeq": "acc-1",
                        "cash": "1500000",
                        "buyingPower": "1200000",
                        "totalEquity": "2100000",
                    }
                }
            )
        if url.endswith("/api/v1/holdings"):
            return FakeResponse(
                payload={
                    "result": [
                        {
                            "symbol": "005930",
                            "quantity": "5",
                            "sellableQuantity": "3",
                            "avgPrice": "10000",
                            "marketValue": "55000",
                            "unrealizedPnl": "5000",
                        },
                        {
                            "symbol": "000660",
                            "quantity": "2",
                            "sellableQuantity": "2",
                            "avgPrice": "50000",
                        },
                    ]
                }
            )
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("requests.request", fake_request)

    client = TossReadOnlyClient(client_id="cid", client_secret="sec", account_seq="acc-1")

    account = client.account_snapshot()
    holdings = client.position_snapshots()

    assert account.account_id == "acc-1"
    assert account.cash == 1_500_000
    assert account.buying_power == 1_200_000
    assert holdings[0].symbol == "005930"
    assert holdings[0].sellable_quantity == 3
    assert holdings[1].avg_price == 50_000


def test_reconcile_account_state_detects_quantity_and_cash_mismatches():
    ledger = ExecutionLedger(initial_cash_krw=1_000_000)
    ledger.seed_position(symbol="005930", quantity=5, avg_price=10_000)
    ledger.seed_position(symbol="000660", quantity=2, avg_price=50_000)

    report = reconcile_account_state(
        desired_ledger=ledger,
        actual_positions=[
            PositionSnapshot(symbol="005930", quantity=3, sellable_quantity=3, avg_price=10_000),
            PositionSnapshot(symbol="035420", quantity=1, sellable_quantity=1, avg_price=80_000),
        ],
        actual_cash_krw=900_000,
    )

    assert report.is_match is False
    assert report.cash_difference_krw == pytest.approx(-100_000)
    assert report.missing_symbols == ["000660"]
    assert report.unexpected_symbols == ["035420"]
    assert report.quantity_mismatches["005930"].desired_quantity == 5
    assert report.quantity_mismatches["005930"].actual_quantity == 3


def test_reconcile_account_state_flags_sellable_quantity_shortfall_for_sell_intent():
    ledger = ExecutionLedger(initial_cash_krw=1_000_000)
    ledger.seed_position(symbol="005930", quantity=5, avg_price=10_000)

    sell_intent = OrderIntent(
        strategy_id="shadow-1",
        symbol="005930",
        side="SELL",
        quantity=4,
        reason="trim",
        mode="paper_auto",
    )

    report = reconcile_account_state(
        desired_ledger=ledger,
        actual_positions=[PositionSnapshot(symbol="005930", quantity=5, sellable_quantity=2, avg_price=10_000)],
        pending_intents=[sell_intent],
    )

    assert report.is_match is False
    assert report.sell_blocked_symbols == ["005930"]
    assert "sellable_quantity_shortfall" in report.violations_by_symbol["005930"]
