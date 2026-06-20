from datetime import datetime, timezone

import pytest

from toss_alpha.data.schema import DisclosureEvent


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="ok"):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


def test_list_disclosures_requires_api_key():
    from toss_alpha.connectors.dart_events import DartEventsClient

    client = DartEventsClient(api_key=None)
    with pytest.raises(ValueError, match="api_key"):
        client.list_disclosures(corp_code="00126380")


def test_list_disclosures_calls_opendart_and_returns_disclosure_events(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append({"url": url, "params": params, "timeout": timeout})
        return FakeResponse(
            payload={
                "status": "000",
                "message": "OK",
                "list": [
                    {
                        "corp_code": "00126380",
                        "stock_code": "005930",
                        "corp_name": "삼성전자",
                        "report_nm": "사업보고서 (2025.12)",
                        "rcept_no": "20260308000123",
                        "rcept_dt": "20260308",
                    }
                ],
            }
        )

    monkeypatch.setattr("requests.get", fake_get)

    from toss_alpha.connectors.dart_events import DartEventsClient

    client = DartEventsClient(api_key="dart-key")
    events = client.list_disclosures(corp_code="00126380", begin_date="20260301", end_date="20260331")

    assert calls[0]["params"]["crtfc_key"] == "dart-key"
    assert calls[0]["params"]["corp_code"] == "00126380"
    assert isinstance(events[0], DisclosureEvent)
    assert events[0].symbol == "005930"
    assert events[0].event_type == "disclosure"
    assert events[0].title == "사업보고서 (2025.12)"
    assert events[0].url.endswith("20260308000123")
    assert events[0].reported_at == datetime(2026, 3, 8, tzinfo=timezone.utc)
    assert events[0].available_at == datetime(2026, 3, 8, tzinfo=timezone.utc)


def test_list_disclosures_raises_on_non_success_status(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return FakeResponse(payload={"status": "013", "message": "No data"})

    monkeypatch.setattr("requests.get", fake_get)

    from toss_alpha.connectors.dart_events import DartEventsClient

    client = DartEventsClient(api_key="dart-key")
    with pytest.raises(RuntimeError, match="OpenDART"):
        client.list_disclosures(corp_code="00126380")
