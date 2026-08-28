from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "collect_opendart_stock_totals.py"


def load_module():
    spec = importlib.util.spec_from_file_location("collect_opendart_stock_totals_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeClient:
    def stock_total_status(self, *, corp_code, business_year, reprt_code):
        assert corp_code == "00126380"
        assert business_year == "2021"
        assert reprt_code == "11013"
        return [
            {
                "rcept_no": "20210517001185",
                "se": "보통부",
                "istc_totqy": "5,969,782,550",
                "tesstk_co": "-",
                "distb_stock_co": "5,969,782,550",
                "stlm_dt": "2021-03-31",
            },
            {
                "rcept_no": "20210517001185",
                "se": "우선주",
                "istc_totqy": "822,886,700",
                "tesstk_co": "-",
                "distb_stock_co": "822,886,700",
                "stlm_dt": "2021-03-31",
            },
        ]


def _manifest(receipt: str = "20210517001185") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "code": "005930",
                "corp_code": "00126380",
                "corp_name": "Samsung",
                "period_end": "2021-03-31",
                "available_at": "2021-05-17",
                "rcept_no": receipt,
                "reprt_code": "11013",
            }
        ]
    )


def test_collector_accepts_only_exact_receipt_and_ordinary_security_row():
    m = load_module()
    rows, issues = m.collect_stock_totals(FakeClient(), _manifest())
    assert len(rows) == 1
    assert not issues
    row = rows[0]
    assert row.rcept_no == "20210517001185"
    assert row.security_type == "보통부"
    assert row.issued_common_shares == 5_969_782_550
    assert row.distributed_common_shares == 5_969_782_550
    assert row.source == "opendart_stock_total_receipt_matched"
    assert row.revision_safe is True


def test_collector_rejects_response_from_different_receipt():
    m = load_module()
    rows, issues = m.collect_stock_totals(FakeClient(), _manifest("20210517009999"))
    assert rows == []
    assert len(issues) == 1
    assert issues[0].issue == "receipt_not_returned_or_ordinary_row_missing"


def test_collector_derives_distribution_only_when_issued_and_treasury_are_both_numeric():
    m = load_module()

    class DerivedClient:
        def stock_total_status(self, **kwargs):
            return [
                {
                    "rcept_no": "20210517001185",
                    "se": "보통주",
                    "istc_totqy": "1000",
                    "tesstk_co": "100",
                    "distb_stock_co": "-",
                    "stlm_dt": "2021-03-31",
                }
            ]

    rows, issues = m.collect_stock_totals(DerivedClient(), _manifest())
    assert not issues
    assert rows[0].distributed_common_shares == 900


def test_cached_receipts_reads_success_and_issue_files(tmp_path):
    m = load_module()
    success = tmp_path / "success.csv"
    issues = tmp_path / "issues.csv"
    pd.DataFrame({"rcept_no": ["A", "B"]}).to_csv(success, index=False)
    pd.DataFrame({"rcept_no": ["B", "C"]}).to_csv(issues, index=False)

    assert m._cached_receipts(success, issues) == {"A", "B", "C"}


def test_incremental_writer_keeps_only_current_manifest_and_replaces_same_receipt(tmp_path):
    m = load_module()
    out = tmp_path / "rows.csv"
    pd.DataFrame(
        [
            {
                "code": "005930",
                "corp_code": "00126380",
                "corp_name": "Old",
                "period_end": "2021-03-31",
                "available_at": "2021-05-17",
                "rcept_no": "KEEP",
                "reprt_code": "11013",
                "security_type": "보통주",
                "issued_common_shares": 1,
                "treasury_common_shares": 0,
                "distributed_common_shares": 1,
                "settlement_date": "2021-03-31",
                "source": "opendart_stock_total_receipt_matched",
                "revision_safe": True,
            },
            {
                "code": "000000",
                "corp_code": "00000000",
                "corp_name": "Stale",
                "period_end": "2020-12-31",
                "available_at": "2021-03-01",
                "rcept_no": "STALE",
                "reprt_code": "11011",
                "security_type": "보통주",
                "issued_common_shares": 1,
                "treasury_common_shares": 0,
                "distributed_common_shares": 1,
                "settlement_date": "2020-12-31",
                "source": "opendart_stock_total_receipt_matched",
                "revision_safe": True,
            },
        ]
    ).to_csv(out, index=False)
    manifest = pd.DataFrame({"rcept_no": ["KEEP", "NEW"]})
    new = m.StockTotalRow(
        code="005930",
        corp_code="00126380",
        corp_name="New",
        period_end="2021-06-30",
        available_at="2021-08-17",
        rcept_no="NEW",
        reprt_code="11012",
        security_type="보통주",
        issued_common_shares=2,
        treasury_common_shares=0,
        distributed_common_shares=2,
        settlement_date="2021-06-30",
    )

    m._write_incremental_results(
        current_manifest=manifest,
        existing_path=out,
        new_items=[new],
        fieldnames=list(m.StockTotalRow.__dataclass_fields__),
    )
    result = pd.read_csv(out, dtype={"rcept_no": str})
    assert set(result["rcept_no"]) == {"KEEP", "NEW"}
    assert "STALE" not in set(result["rcept_no"])
