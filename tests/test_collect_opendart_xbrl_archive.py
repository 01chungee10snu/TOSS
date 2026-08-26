from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "collect_opendart_xbrl_archive.py"


def load_module():
    spec = importlib.util.spec_from_file_location("collect_opendart_xbrl_archive_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeClient:
    def __init__(self):
        self.saved = []

    def corp_code_table(self):
        return [
            {"stock_code": "005930", "corp_code": "00126380", "corp_name": "Samsung"},
            {"stock_code": "000660", "corp_code": "00164779", "corp_name": "SK hynix"},
        ]

    def list_filings(self, *, corp_code, begin_date, end_date):
        if corp_code == "00126380":
            return [
                {"report_nm": "분기보고서 (2025.03)", "rcept_no": "20250515000123", "rcept_dt": "20250515"},
                {"report_nm": "[기재정정]분기보고서 (2025.03)", "rcept_no": "20250520000456", "rcept_dt": "20250520"},
                {"report_nm": "주요사항보고서", "rcept_no": "20250522000001", "rcept_dt": "20250522"},
            ]
        return []

    def save_xbrl(self, *, rcept_no, reprt_code, path):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fake-zip")
        self.saved.append((rcept_no, reprt_code, target))
        return target


def test_collect_archive_preserves_original_and_amended_receipt_versions(tmp_path):
    m = load_module()
    client = FakeClient()
    rows = m.collect_archive(
        client,
        codes=["5930", "000660"],
        begin_date="2025-01-01",
        end_date="2025-12-31",
        raw_dir=tmp_path / "raw",
    )

    assert len(rows) == 2
    assert [row.rcept_no for row in rows] == ["20250515000123", "20250520000456"]
    assert rows[0].available_at == "2025-05-15"
    assert rows[0].period_end == "2025-03-31"
    assert rows[0].reprt_code == "11013"
    assert rows[0].revision_safe is True
    assert rows[0].is_amendment is False
    assert rows[1].is_amendment is True
    assert len(client.saved) == 2


def test_collect_archive_does_not_redownload_existing_receipt(tmp_path):
    m = load_module()
    client = FakeClient()
    existing = tmp_path / "raw" / "005930" / "20250515000123_11013.zip"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"already")

    m.collect_archive(
        client,
        codes=["005930"],
        begin_date="2025-01-01",
        end_date="2025-12-31",
        raw_dir=tmp_path / "raw",
    )
    saved_receipts = {item[0] for item in client.saved}
    assert "20250515000123" not in saved_receipts
    assert "20250520000456" in saved_receipts


def test_write_manifest_is_stable_and_contains_pit_provenance(tmp_path):
    m = load_module()
    rows = m.collect_archive(
        FakeClient(),
        codes=["005930"],
        begin_date="2025-01-01",
        end_date="2025-12-31",
        raw_dir=tmp_path / "raw",
    )
    manifest = tmp_path / "manifest.csv"
    m.write_manifest(rows, manifest)
    text = manifest.read_text(encoding="utf-8")
    assert "rcept_no" in text
    assert "available_at" in text
    assert "revision_safe" in text
    assert "opendart_receipt_xbrl" in text
