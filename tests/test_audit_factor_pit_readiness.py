from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_factor_pit_readiness.py"


def load_module():
    spec = importlib.util.spec_from_file_location("audit_factor_pit_readiness_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _pit_panel() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2025-05-16", "2025-05-16"],
            "code": ["005930", "111111"],
            "delisted": [None, "2025-06-30"],
        }
    )


def _opendart() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": ["005930"],
            "period_end": ["2025-03-31"],
            "available_at": ["2025-05-15"],
            "source": ["opendart_receipt_xbrl"],
            "is_estimate": [False],
            "revision_safe": [True],
            "bps": [50000.0],
            "revenue": [100.0],
            "rcept_no": ["20250515000123"],
            "reprt_code": ["11013"],
        }
    )


def test_loader_prefers_opendart_receipt_versioned_file(tmp_path):
    m = load_module()
    dart = tmp_path / "dart.csv"
    naver = tmp_path / "naver.csv"
    _opendart().to_csv(dart, index=False)
    pd.DataFrame({"code": ["005930"], "year": [2025], "month": [3], "revenue": [1], "bps": [1]}).to_csv(naver, index=False)

    candidate, source = m.load_preferred_candidate(opendart_path=dart, naver_path=naver)
    report = m.build_readiness_report(candidate, _pit_panel(), source=source, opendart_key_present=False)

    assert source == "opendart_receipt_xbrl"
    assert report["promotion"]["factor_backtest_allowed"] is True
    assert report["promotion"]["hml_cma_live_promotion_allowed"] is False


def test_loader_falls_back_to_naver_only_as_blocked_diagnostic(tmp_path):
    m = load_module()
    naver = tmp_path / "naver.csv"
    pd.DataFrame(
        {
            "code": ["005930"],
            "period": ["2025.03"],
            "year": [2025],
            "month": [3],
            "revenue": [100],
            "bps": [50000],
        }
    ).to_csv(naver, index=False)

    candidate, source = m.load_preferred_candidate(opendart_path=tmp_path / "missing.csv", naver_path=naver)
    report = m.build_readiness_report(candidate, _pit_panel(), source=source, opendart_key_present=False)

    assert source == "naver_current_view"
    assert report["promotion"]["factor_backtest_allowed"] is False
    assert report["promotion"]["status"] == "BLOCKED_TRUE_PIT_INPUTS"


def test_incomplete_opendart_is_not_silently_replaced_by_naver(tmp_path):
    m = load_module()
    dart = tmp_path / "dart.csv"
    naver = tmp_path / "naver.csv"
    bad = _opendart()
    bad.loc[0, "bps"] = None
    bad.to_csv(dart, index=False)
    _opendart().to_csv(naver, index=False)

    candidate, source = m.load_preferred_candidate(opendart_path=dart, naver_path=naver)
    report = m.build_readiness_report(candidate, _pit_panel(), source=source, opendart_key_present=True)

    assert source == "opendart_receipt_xbrl"
    assert report["promotion"]["factor_backtest_allowed"] is False
    assert any(reason.startswith("missing_factor_value_rows:") for reason in report["contract"]["reasons"])
