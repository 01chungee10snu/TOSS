from __future__ import annotations

import pandas as pd

from toss_alpha.research.factor_pit import (
    intersect_snapshot_with_universe,
    naver_current_view_candidate,
    pit_factor_snapshot,
    pit_snapshot,
    tradable_codes_on,
    validate_pit_contract,
)


def _historical_panel() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2025-05-16", "2025-05-16", "2025-05-15"],
            "code": ["005930", "111111", "005930"],
            "Close": [60000, 1000, 59000],
            "delisted": [None, "2025-06-30", None],
        }
    )


def _true_pit_fundamentals() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": ["005930", "111111"],
            "period_end": ["2025-03-31", "2025-03-31"],
            "available_at": ["2025-05-15", "2025-05-15"],
            "source": ["opendart_original_filing", "opendart_original_filing"],
            "is_estimate": [False, False],
            "revision_safe": [True, True],
            "bps": [50000, 700],
            "revenue": [100, 20],
        }
    )


def test_true_pit_contract_requires_availability_revision_safety_and_delisted_universe():
    result = validate_pit_contract(_true_pit_fundamentals(), _historical_panel())
    assert result.eligible is True
    assert result.status == "TRUE_PIT_ELIGIBLE"
    assert result.reasons == ()
    assert result.stats["delisted_codes"] == 1


def test_true_pit_contract_blocks_rows_with_missing_required_factor_values():
    fund = _true_pit_fundamentals()
    fund.loc[0, "bps"] = None
    result = validate_pit_contract(fund, _historical_panel())

    assert result.eligible is False
    assert "missing_factor_value_rows:bps=1" in result.reasons
    assert result.stats["missing_factor_value_rows"]["bps"] == 1


def test_naver_current_view_fails_true_pit_contract_even_with_good_backtest_fields():
    raw = pd.DataFrame(
        {
            "code": ["005930"],
            "period": ["2025.03"],
            "year": [2025],
            "month": [3],
            "revenue": [100],
            "bps": [50000],
        }
    )
    candidate = naver_current_view_candidate(raw)
    result = validate_pit_contract(candidate, _historical_panel())

    assert result.eligible is False
    assert result.status == "BLOCKED_PIT_CONTRACT"
    assert "actual_filing_availability_missing" in result.reasons
    assert "revision_safe_historical_values_missing" in result.reasons
    assert set(candidate["source"]) == {"naver_current_view"}


def test_pit_snapshot_uses_only_values_available_by_cutoff_and_latest_revision():
    fund = pd.DataFrame(
        {
            "code": ["005930", "005930", "005930"],
            "period_end": ["2024-12-31", "2024-12-31", "2025-03-31"],
            "available_at": ["2025-03-10", "2025-03-20", "2025-05-15"],
            "source": ["original", "amendment", "original"],
            "is_estimate": [False, False, False],
            "revision_safe": [True, True, True],
            "bps": [100, 110, 120],
            "revenue": [10, 11, 12],
        }
    )

    before_amendment = pit_snapshot(fund, "2025-03-15")
    after_amendment = pit_snapshot(fund, "2025-03-25")
    after_q1 = pit_snapshot(fund, "2025-05-16")

    assert before_amendment.iloc[0]["bps"] == 100
    assert after_amendment.iloc[0]["bps"] == 110
    assert after_q1.iloc[0]["bps"] == 120


def test_factor_snapshot_recomputes_yoy_only_after_prior_year_amendment_is_available():
    fund = pd.DataFrame(
        {
            "code": ["005930", "005930", "005930"],
            "period_end": ["2024-03-31", "2024-03-31", "2025-03-31"],
            "available_at": ["2024-05-15", "2025-05-18", "2025-05-15"],
            "source": ["original", "amendment", "original"],
            "is_estimate": [False, False, False],
            "revision_safe": [True, True, True],
            "reprt_code": ["11013", "11013", "11013"],
            "revenue_basis": ["quarter", "quarter", "quarter"],
            "rcept_no": ["202405150001", "202505180001", "202505150001"],
            "bps": [100, 110, 120],
            "revenue": [100, 110, 150],
        }
    )

    before_prior_amendment = pit_factor_snapshot(fund, "2025-05-16")
    after_prior_amendment = pit_factor_snapshot(fund, "2025-05-19")

    assert before_prior_amendment.iloc[0]["revenue_yoy"] == 0.5
    assert round(float(after_prior_amendment.iloc[0]["revenue_yoy"]), 6) == round(150 / 110 - 1, 6)
    assert before_prior_amendment.iloc[0]["revenue_prior_rcept_no"] == "202405150001"
    assert after_prior_amendment.iloc[0]["revenue_prior_rcept_no"] == "202505180001"


def test_factor_snapshot_can_intersect_with_historical_tradeable_universe():
    fund = _true_pit_fundamentals().copy()
    fund["reprt_code"] = "11013"
    fund["revenue_basis"] = "quarter"
    older = fund.copy()
    older["period_end"] = "2024-03-31"
    older["available_at"] = "2024-05-15"
    older["revenue"] = [80, 10]
    combined = pd.concat([older, fund], ignore_index=True)

    snap = pit_factor_snapshot(combined, "2025-05-16", universe_panel=_historical_panel())
    assert set(snap["code"]) == {"005930", "111111"}
    assert snap["revenue_yoy"].notna().all()


def test_strict_snapshot_excludes_revision_unsafe_rows():
    fund = _true_pit_fundamentals()
    fund.loc[0, "revision_safe"] = False
    strict = pit_snapshot(fund, "2025-05-16", require_revision_safe=True)
    exploratory = pit_snapshot(fund, "2025-05-16", require_revision_safe=False)
    assert "005930" not in set(strict["code"])
    assert "005930" in set(exploratory["code"])


def test_historical_universe_intersection_keeps_only_tradeable_names_on_date():
    snapshot = _true_pit_fundamentals()
    allowed = tradable_codes_on(_historical_panel(), "2025-05-16")
    filtered = intersect_snapshot_with_universe(snapshot, _historical_panel(), "2025-05-15")

    assert allowed == {"005930", "111111"}
    assert set(filtered["code"]) == {"005930"}
