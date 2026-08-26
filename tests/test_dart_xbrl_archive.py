from __future__ import annotations

import io
import zipfile

import pytest

from toss_alpha.connectors.dart_xbrl_archive import (
    DartXbrlArchiveClient,
    is_periodic_report,
    period_end_from_report_name,
    report_code_from_name,
)


class FakeResponse:
    def __init__(self, *, payload=None, content=b"", status_code=200):
        self._payload = payload
        self.content = content
        self.status_code = status_code
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload or {}


def _zip_bytes(name: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(name, content)
    return buf.getvalue()


def test_report_code_and_period_end_are_parsed_from_periodic_report_titles():
    assert report_code_from_name("사업보고서 (2025.12)") == "11011"
    assert report_code_from_name("반기보고서 (2025.06)") == "11012"
    assert report_code_from_name("분기보고서 (2025.03)") == "11013"
    assert report_code_from_name("[기재정정]분기보고서 (2025.09)") == "11014"
    assert report_code_from_name("주요사항보고서") is None
    assert period_end_from_report_name("사업보고서 (2025.12)") == "2025-12-31"
    assert period_end_from_report_name("분기보고서 (2024.09)") == "2024-09-30"
    assert is_periodic_report("분기보고서 (2025.03)") is True


def test_corp_code_table_parses_zip_archive(monkeypatch):
    xml = b"""<?xml version='1.0' encoding='UTF-8'?><result><list><corp_code>00126380</corp_code><corp_name>Samsung</corp_name><stock_code>005930</stock_code><modify_date>20260101</modify_date></list></result>"""
    monkeypatch.setattr(
        "requests.get",
        lambda *args, **kwargs: FakeResponse(content=_zip_bytes("CORPCODE.xml", xml)),
    )
    client = DartXbrlArchiveClient(api_key="key")
    rows = client.corp_code_table()
    assert rows == [
        {
            "corp_code": "00126380",
            "corp_name": "Samsung",
            "stock_code": "005930",
            "modify_date": "20260101",
        }
    ]


def test_list_filings_paginates_and_preserves_receipt_metadata(monkeypatch):
    calls = []

    def fake_get(_url, params=None, timeout=None):
        calls.append(dict(params or {}))
        page = int(params["page_no"])
        return FakeResponse(
            payload={
                "status": "000",
                "total_page": 2,
                "list": [
                    {
                        "rcept_no": f"20250{page}01000001",
                        "rcept_dt": f"20250{page}01",
                        "report_nm": "분기보고서 (2025.03)" if page == 1 else "반기보고서 (2025.06)",
                    }
                ],
            }
        )

    monkeypatch.setattr("requests.get", fake_get)
    client = DartXbrlArchiveClient(api_key="key")
    rows = client.list_filings(corp_code="00126380", begin_date="2025-01-01", end_date="2025-12-31")
    assert len(rows) == 2
    assert calls[0]["bgn_de"] == "20250101"
    assert calls[1]["page_no"] == 2
    assert rows[0]["rcept_no"] == "20250101000001"


def test_download_xbrl_requires_receipt_versioned_zip(monkeypatch):
    archive = _zip_bytes("sample.xbrl", b"<xbrl/>")
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append({"url": url, "params": dict(params or {})})
        return FakeResponse(content=archive)

    monkeypatch.setattr("requests.get", fake_get)
    client = DartXbrlArchiveClient(api_key="key")
    data = client.download_xbrl(rcept_no="20260308000123", reprt_code="11011")
    assert data == archive
    assert calls[0]["url"].endswith("/fnlttXbrl.xml")
    assert calls[0]["params"]["rcept_no"] == "20260308000123"
    assert calls[0]["params"]["reprt_code"] == "11011"


def test_download_xbrl_rejects_xml_error_response(monkeypatch):
    monkeypatch.setattr(
        "requests.get",
        lambda *args, **kwargs: FakeResponse(content=b"<result><status>013</status><message>No data</message></result>"),
    )
    client = DartXbrlArchiveClient(api_key="key")
    with pytest.raises(RuntimeError, match="not a zip"):
        client.download_xbrl(rcept_no="20260308000123", reprt_code="11011")


def test_archive_client_requires_api_key():
    client = DartXbrlArchiveClient(api_key="")
    with pytest.raises(ValueError, match="api_key"):
        client.corp_code_table()
