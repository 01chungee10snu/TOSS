from __future__ import annotations

import pandas as pd

from toss_alpha.research.factor_pit import (
    intersect_snapshot_with_universe,
    naver_current_view_candidate,
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
