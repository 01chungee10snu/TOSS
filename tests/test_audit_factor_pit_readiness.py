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
            "assets": [1_000_000.0],
            "book_equity": [500_000.0],
            "operating_income": [50_000.0],
            "operating_profitability_proxy": [0.10],
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
    report = m.build_readiness_report(
        candidate,
        _pit_panel(),
        source=source,
        opendart_key_present=False,
        min_universe_codes=1,
        min_hml_cma_ready_row_share=1.0,
        min_profitability_ready_row_share=1.0,
        required_history_start=pd.Timestamp("2025-03-31"),
        required_available_start=pd.Timestamp("2025-05-15"),
        min_factor_ready_codes_per_rebalance=0,
    )

    assert source == "opendart_receipt_xbrl"
    assert report["promotion"]["factor_backtest_allowed"] is True
    assert report["promotion"]["hml_cma_backtest_allowed"] is True
    assert report["promotion"]["profitability_backtest_allowed"] is True
    assert report["promotion"]["hml_cma_live_promotion_allowed"] is False
    assert report["promotion"]["profitability_live_promotion_allowed"] is False


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
    assert report["promotion"]["hml_cma_backtest_allowed"] is False
    assert report["promotion"]["profitability_backtest_allowed"] is False
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
    report = m.build_readiness_report(
        candidate,
        _pit_panel(),
        source=source,
        opendart_key_present=True,
        min_universe_codes=1,
        min_hml_cma_ready_row_share=1.0,
        min_profitability_ready_row_share=1.0,
        required_history_start=pd.Timestamp("2025-03-31"),
        required_available_start=pd.Timestamp("2025-05-15"),
        min_factor_ready_codes_per_rebalance=0,
    )

    assert source == "opendart_receipt_xbrl"
    assert report["promotion"]["factor_backtest_allowed"] is False
    assert any(reason.startswith("missing_factor_value_rows:") for reason in report["contract"]["reasons"])


def test_default_universe_coverage_blocks_small_recent_pilot_even_when_rows_are_clean():
    m = load_module()
    candidate = _opendart()
    report = m.build_readiness_report(
        candidate,
        _pit_panel(),
        source="opendart_receipt_xbrl",
        opendart_key_present=True,
    )

    coverage = report["universe_coverage"]
    assert coverage["combined"]["passed"] is False
    assert coverage["observed"]["codes"] == 1
    assert coverage["observed"]["hml_cma_ready_row_share"] == 1.0
    assert any(reason.startswith("universe_codes_below_minimum:") for reason in coverage["combined"]["reasons"])
    assert any(reason.startswith("fundamental_period_history_starts_too_late:") for reason in coverage["combined"]["reasons"])
    assert any(reason.startswith("filing_availability_history_starts_too_late:") for reason in coverage["combined"]["reasons"])
    assert report["contracts"]["provenance"]["eligible"] is True
    assert report["asof_rebalance_coverage"]["passed"] is False
    assert report["promotion"]["factor_backtest_allowed"] is False


def test_asof_rebalance_coverage_rejects_current_survivor_basket_with_no_early_factor_history():
    m = load_module()
    candidate = _opendart()
    panel = pd.DataFrame(
        {
            "date": ["2024-06-28", "2024-06-28", "2025-05-16", "2025-05-16"],
            "code": ["005930", "111111", "005930", "111111"],
            "delisted": [None, "2024-12-31", None, "2024-12-31"],
        }
    )

    coverage = m.assess_asof_rebalance_coverage(
        candidate,
        panel,
        required_rebalance_start=pd.Timestamp("2024-06-01"),
        min_factor_ready_codes=1,
    )

    assert coverage["passed"] is False
    assert coverage["hml_cma"]["passed"] is False
    assert coverage["profitability"]["passed"] is False
    assert coverage["checkpoint_count"] == 2
    assert coverage["checkpoints"][0]["hml_cma_ready_codes"] == 0
    assert coverage["checkpoints"][0]["profitability_ready_codes"] == 0
    assert any(reason.startswith("hml_cma_rebalance_checkpoints_below_minimum:") for reason in coverage["reasons"])


def test_profitability_asof_gate_can_pass_while_hml_cma_remains_blocked():
    m = load_module()
    candidate = pd.concat(
        [
            _opendart().assign(bps=None, period_end="2024-03-31", available_at="2024-05-15", rcept_no="20240515000123"),
            _opendart().assign(bps=None),
        ],
        ignore_index=True,
    )
    panel = pd.DataFrame(
        {
            "date": ["2024-06-28", "2025-05-16"],
            "code": ["005930", "005930"],
            "delisted": [None, None],
        }
    )

    coverage = m.assess_asof_rebalance_coverage(
        candidate,
        panel,
        required_rebalance_start=pd.Timestamp("2024-06-01"),
        min_factor_ready_codes=1,
    )

    assert coverage["hml_cma"]["passed"] is False
    assert coverage["profitability"]["passed"] is True
    assert coverage["minimum_observed_profitability_ready_codes"] == 1
