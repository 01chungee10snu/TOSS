from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

import pandas as pd

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_opendart_pit_fundamentals.py"


def load_module():
    spec = importlib.util.spec_from_file_location("build_opendart_pit_fundamentals_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _xbrl(*, equity: int, revenue: int, operating_income: int = 60_000, net_income: int = 45_000, shares: int = 100_000) -> bytes:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:ifrs-full="http://ifrs" xmlns:dart="http://dart" xmlns:iso4217="http://iso">
  <xbrli:unit id="krw"><xbrli:measure>iso4217:KRW</xbrli:measure></xbrli:unit>
  <xbrli:unit id="shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>
  <xbrli:context id="i"><xbrli:entity><xbrli:identifier scheme="dart">001</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2025-03-31</xbrli:instant></xbrli:period></xbrli:context>
  <xbrli:context id="q"><xbrli:entity><xbrli:identifier scheme="dart">001</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-03-31</xbrli:endDate></xbrli:period></xbrli:context>
  <ifrs-full:Assets contextRef="i" unitRef="krw">2000000</ifrs-full:Assets>
  <ifrs-full:Equity contextRef="i" unitRef="krw">{equity}</ifrs-full:Equity>
  <ifrs-full:Revenue contextRef="q" unitRef="krw">{revenue}</ifrs-full:Revenue>
  <ifrs-full:ProfitLossFromOperatingActivities contextRef="q" unitRef="krw">{operating_income}</ifrs-full:ProfitLossFromOperatingActivities>
  <ifrs-full:ProfitLossAttributableToOwnersOfParent contextRef="q" unitRef="krw">{net_income}</ifrs-full:ProfitLossAttributableToOwnersOfParent>
  <dart:NumberOfSharesOutstanding contextRef="i" unitRef="shares">{shares}</dart:NumberOfSharesOutstanding>
</xbrli:xbrl>'''.encode()


def _archive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("company_consolidated.xbrl", payload)


def _manifest() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "code": "005930",
                "corp_code": "00126380",
                "corp_name": "Samsung",
                "period_end": "2025-03-31",
                "available_at": "2025-05-15",
                "rcept_no": "20250515000123",
                "reprt_code": "11013",
                "source": "opendart_receipt_xbrl",
                "revision_safe": True,
                "is_amendment": False,
                "archive_path": "005930/20250515000123_11013.zip",
            },
            {
                "code": "005930",
                "corp_code": "00126380",
                "corp_name": "Samsung",
                "period_end": "2025-03-31",
                "available_at": "2025-05-20",
                "rcept_no": "20250520000456",
                "reprt_code": "11013",
                "source": "opendart_receipt_xbrl",
                "revision_safe": True,
                "is_amendment": True,
                "archive_path": "005930/20250520000456_11013.zip",
            },
        ]
    )


def test_builder_preserves_original_and_amendment_as_distinct_available_versions(tmp_path):
    m = load_module()
    raw = tmp_path / "raw"
    _archive(raw / "005930/20250515000123_11013.zip", _xbrl(equity=900_000, revenue=300_000))
    _archive(raw / "005930/20250520000456_11013.zip", _xbrl(equity=950_000, revenue=320_000))

    frame = m.build_rows(_manifest(), raw_dir=raw)

    assert len(frame) == 2
    assert list(frame["available_at"]) == ["2025-05-15", "2025-05-20"]
    assert list(frame["book_equity"]) == [900_000, 950_000]
    assert list(frame["revenue"]) == [300_000, 320_000]
    assert list(frame["bps"]) == [9.0, 9.5]
    assert list(frame["operating_income"]) == [60_000, 60_000]
    assert list(frame["net_income"]) == [45_000, 45_000]
    assert round(float(frame.iloc[0]["operating_profitability_proxy"]), 6) == round(60_000 / 900_000, 6)
    assert list(frame["profitability_status"]) == ["READY", "READY"]
    assert list(frame["parse_status"]) == ["READY", "READY"]
    assert list(frame["is_amendment"]) == [False, True]


def test_builder_missing_archive_is_retained_and_fails_factor_completeness(tmp_path):
    m = load_module()
    manifest = _manifest().iloc[:1].copy()
    frame = m.build_rows(manifest, raw_dir=tmp_path / "raw")

    assert frame.iloc[0]["parse_status"] == "MISSING_ARCHIVE"
    assert pd.isna(frame.iloc[0]["bps"])
    assert pd.isna(frame.iloc[0]["revenue"])

    panel = pd.DataFrame(
        {
            "date": ["2025-05-16", "2025-05-16"],
            "code": ["005930", "111111"],
            "delisted": [None, "2025-06-30"],
        }
    )
    audit = m.build_audit(frame, pit_panel=panel)
    assert audit["pit_contract"]["eligible"] is False
    assert any(reason.startswith("missing_factor_value_rows:") for reason in audit["pit_contract"]["reasons"])


def test_builder_complete_revision_safe_rows_can_satisfy_pit_input_contract(tmp_path):
    m = load_module()
    raw = tmp_path / "raw"
    _archive(raw / "005930/20250515000123_11013.zip", _xbrl(equity=900_000, revenue=300_000))
    frame = m.build_rows(_manifest().iloc[:1].copy(), raw_dir=raw)
    panel = pd.DataFrame(
        {
            "date": ["2025-05-16", "2025-05-16"],
            "code": ["005930", "111111"],
            "delisted": [None, "2025-06-30"],
        }
    )

    audit = m.build_audit(frame, pit_panel=panel)
    assert audit["ready_rows"] == 1
    assert audit["profitability_ready_rows"] == 1
    assert audit["latest_value_collapsing_performed"] is False
    assert audit["pit_contract"]["status"] == "TRUE_PIT_ELIGIBLE"
    assert audit["profitability_pit_contract"]["status"] == "TRUE_PIT_ELIGIBLE"
