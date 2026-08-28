"""Audit whether factor research inputs qualify for a true point-in-time backtest.

Preference order is strict:
1. receipt-versioned OpenDART fundamentals built from archived XBRL packages;
2. existing Naver current-view fundamentals only as a fail-closed diagnostic.

Passing this input contract never promotes a strategy to live trading.  It only
allows a new independent OOS factor backtest to begin.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from toss_alpha.research.factor_pit import (
    naver_current_view_candidate,
    normalize_universe_panel,
    pit_factor_snapshot,
    validate_pit_contract,
)

ROOT = Path(__file__).resolve().parents[1]
NAVER_CSV = ROOT / "reports" / "backtests" / "fundamental" / "naver_quarterly_fundamentals.csv"
OPENDART_PIT_CSV = ROOT / "reports" / "backtests" / "fundamental" / "opendart_pit_fundamentals.csv"
PIT_PANEL_CSV = ROOT / "reports" / "backtests" / "pit_full_universe_2022-01-01_2026_ohlcv_panel.csv"
OUT = ROOT / "reports" / "validation" / "factor_pit_readiness_latest.json"
MIN_UNIVERSE_CODES = 100
MIN_HML_CMA_READY_ROW_SHARE = 0.60
MIN_PROFITABILITY_READY_ROW_SHARE = 0.90
REQUIRED_HISTORY_START = pd.Timestamp("2021-03-31")
REQUIRED_AVAILABLE_START = pd.Timestamp("2021-05-31")
REQUIRED_REBALANCE_START = pd.Timestamp("2022-06-30")
MIN_FACTOR_READY_CODES_PER_REBALANCE = 100


def _factor_ready_mask(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.Series:
    if frame.empty or any(column not in frame.columns for column in columns):
        return pd.Series(False, index=frame.index, dtype=bool)
    mask = pd.Series(True, index=frame.index, dtype=bool)
    for column in columns:
        mask &= pd.to_numeric(frame[column], errors="coerce").notna()
    return mask


def _factor_contract(candidate: pd.DataFrame, pit_panel: pd.DataFrame, columns: tuple[str, ...]):
    mask = _factor_ready_mask(candidate, columns)
    if not bool(mask.any()):
        return validate_pit_contract(candidate, pit_panel, required_value_columns=columns)
    return validate_pit_contract(candidate.loc[mask].copy(), pit_panel, required_value_columns=columns)


def assess_universe_coverage(
    candidate: pd.DataFrame,
    *,
    min_universe_codes: int = MIN_UNIVERSE_CODES,
    min_hml_cma_ready_row_share: float = MIN_HML_CMA_READY_ROW_SHARE,
    min_profitability_ready_row_share: float = MIN_PROFITABILITY_READY_ROW_SHARE,
    required_history_start: pd.Timestamp = REQUIRED_HISTORY_START,
    required_available_start: pd.Timestamp = REQUIRED_AVAILABLE_START,
) -> dict[str, Any]:
    rows = int(len(candidate))
    codes = int(candidate["code"].astype(str).str.zfill(6).nunique()) if "code" in candidate.columns else 0
    period = pd.to_datetime(candidate.get("period_end", pd.Series(dtype=object)), errors="coerce")
    available = pd.to_datetime(candidate.get("available_at", pd.Series(dtype=object)), errors="coerce")
    hml_mask = _factor_ready_mask(candidate, ("bps", "assets"))
    profitability_mask = _factor_ready_mask(candidate, ("book_equity", "operating_income"))
    hml_share = float(hml_mask.mean()) if rows else 0.0
    profitability_share = float(profitability_mask.mean()) if rows else 0.0
    history_start = period.min() if period.notna().any() else pd.NaT
    available_start = available.min() if available.notna().any() else pd.NaT

    common_reasons: list[str] = []
    if codes < int(min_universe_codes):
        common_reasons.append(f"universe_codes_below_minimum:{codes}<{int(min_universe_codes)}")
    if pd.isna(history_start) or pd.Timestamp(history_start) > pd.Timestamp(required_history_start):
        actual = "missing" if pd.isna(history_start) else pd.Timestamp(history_start).date().isoformat()
        common_reasons.append(
            f"fundamental_period_history_starts_too_late:{actual}>{pd.Timestamp(required_history_start).date().isoformat()}"
        )
    if pd.isna(available_start) or pd.Timestamp(available_start) > pd.Timestamp(required_available_start):
        actual = "missing" if pd.isna(available_start) else pd.Timestamp(available_start).date().isoformat()
        common_reasons.append(
            f"filing_availability_history_starts_too_late:{actual}>{pd.Timestamp(required_available_start).date().isoformat()}"
        )

    hml_reasons = list(common_reasons)
    if hml_share < float(min_hml_cma_ready_row_share):
        hml_reasons.append(
            f"hml_cma_ready_row_share_below_minimum:{hml_share:.4f}<{float(min_hml_cma_ready_row_share):.4f}"
        )
    profitability_reasons = list(common_reasons)
    if profitability_share < float(min_profitability_ready_row_share):
        profitability_reasons.append(
            "profitability_ready_row_share_below_minimum:"
            f"{profitability_share:.4f}<{float(min_profitability_ready_row_share):.4f}"
        )

    return {
        "thresholds": {
            "min_universe_codes": int(min_universe_codes),
            "min_hml_cma_ready_row_share": float(min_hml_cma_ready_row_share),
            "min_profitability_ready_row_share": float(min_profitability_ready_row_share),
            "required_history_start_on_or_before": pd.Timestamp(required_history_start).date().isoformat(),
            "required_available_start_on_or_before": pd.Timestamp(required_available_start).date().isoformat(),
        },
        "observed": {
            "rows": rows,
            "codes": codes,
            "history_start": None if pd.isna(history_start) else pd.Timestamp(history_start).date().isoformat(),
            "history_end": None if not period.notna().any() else pd.Timestamp(period.max()).date().isoformat(),
            "available_start": None if pd.isna(available_start) else pd.Timestamp(available_start).date().isoformat(),
            "available_end": None if not available.notna().any() else pd.Timestamp(available.max()).date().isoformat(),
            "hml_cma_ready_rows": int(hml_mask.sum()),
            "hml_cma_ready_row_share": round(hml_share, 6),
            "profitability_ready_rows": int(profitability_mask.sum()),
            "profitability_ready_row_share": round(profitability_share, 6),
        },
        "hml_cma": {"passed": not hml_reasons, "reasons": hml_reasons},
        "profitability": {"passed": not profitability_reasons, "reasons": profitability_reasons},
        "combined": {
            "passed": not hml_reasons and not profitability_reasons,
            "reasons": list(dict.fromkeys(hml_reasons + profitability_reasons)),
        },
    }


def assess_asof_rebalance_coverage(
    candidate: pd.DataFrame,
    pit_panel: pd.DataFrame,
    *,
    required_rebalance_start: pd.Timestamp = REQUIRED_REBALANCE_START,
    min_factor_ready_codes: int = MIN_FACTOR_READY_CODES_PER_REBALANCE,
) -> dict[str, Any]:
    """Check every historical month-end against the actual tradable universe."""
    panel = normalize_universe_panel(pit_panel)
    valid_dates = pd.DatetimeIndex(panel["_pit_date"].dropna().unique()).sort_values()
    valid_dates = valid_dates[valid_dates >= pd.Timestamp(required_rebalance_start).normalize()]
    if len(valid_dates) == 0:
        no_data_reason = ["no_historical_rebalance_checkpoints"]
        return {
            "passed": False,
            "reasons": no_data_reason,
            "hml_cma": {"passed": False, "reasons": no_data_reason},
            "profitability": {"passed": False, "reasons": no_data_reason},
            "thresholds": {
                "required_rebalance_start_on_or_after": pd.Timestamp(required_rebalance_start).date().isoformat(),
                "min_factor_ready_codes_per_rebalance": int(min_factor_ready_codes),
            },
            "checkpoint_count": 0,
            "checkpoints": [],
        }

    date_series = pd.Series(valid_dates, index=valid_dates)
    month_ends = [pd.Timestamp(day).normalize() for day in date_series.groupby(date_series.index.to_period("M")).max()]
    panel_at_checkpoints = panel[panel["_pit_date"].isin(month_ends)]
    allowed_by_date = {
        pd.Timestamp(day).normalize(): set(group["code"].astype(str))
        for day, group in panel_at_checkpoints.groupby("_pit_date")
    }

    checkpoints: list[dict[str, Any]] = []
    hml_cma_failures = 0
    profitability_failures = 0
    for day in month_ends:
        snapshot = pit_factor_snapshot(candidate, day, require_revision_safe=True)
        if snapshot.empty:
            hml_count = 0
            profitability_count = 0
        else:
            snapshot = snapshot.copy()
            snapshot["code"] = snapshot["code"].astype(str).str.zfill(6)
            snapshot = snapshot[snapshot["code"].isin(allowed_by_date.get(day, set()))]
            hml_count = int(_factor_ready_mask(snapshot, ("bps", "asset_growth")).sum())
            profitability_count = int(_factor_ready_mask(snapshot, ("operating_profitability_proxy",)).sum())
        hml_passed = hml_count >= int(min_factor_ready_codes)
        profitability_passed = profitability_count >= int(min_factor_ready_codes)
        hml_cma_failures += int(not hml_passed)
        profitability_failures += int(not profitability_passed)
        checkpoints.append(
            {
                "date": day.date().isoformat(),
                "hml_cma_ready_codes": hml_count,
                "profitability_ready_codes": profitability_count,
                "hml_cma_passed": hml_passed,
                "profitability_passed": profitability_passed,
            }
        )

    hml_reasons: list[str] = []
    profitability_reasons: list[str] = []
    if hml_cma_failures:
        hml_reasons.append(f"hml_cma_rebalance_checkpoints_below_minimum:{hml_cma_failures}/{len(checkpoints)}")
    if profitability_failures:
        profitability_reasons.append(
            f"profitability_rebalance_checkpoints_below_minimum:{profitability_failures}/{len(checkpoints)}"
        )
    reasons = list(dict.fromkeys(hml_reasons + profitability_reasons))
    return {
        "passed": not reasons,
        "reasons": reasons,
        "hml_cma": {"passed": not hml_reasons, "reasons": hml_reasons},
        "profitability": {"passed": not profitability_reasons, "reasons": profitability_reasons},
        "thresholds": {
            "required_rebalance_start_on_or_after": pd.Timestamp(required_rebalance_start).date().isoformat(),
            "min_factor_ready_codes_per_rebalance": int(min_factor_ready_codes),
        },
        "checkpoint_count": len(checkpoints),
        "minimum_observed_hml_cma_ready_codes": min(item["hml_cma_ready_codes"] for item in checkpoints),
        "minimum_observed_profitability_ready_codes": min(
            item["profitability_ready_codes"] for item in checkpoints
        ),
        "checkpoints": checkpoints,
    }


def build_readiness_report(
    candidate: pd.DataFrame,
    pit_panel: pd.DataFrame,
    *,
    source: str,
    opendart_key_present: bool,
    min_universe_codes: int = MIN_UNIVERSE_CODES,
    min_hml_cma_ready_row_share: float = MIN_HML_CMA_READY_ROW_SHARE,
    min_profitability_ready_row_share: float = MIN_PROFITABILITY_READY_ROW_SHARE,
    required_history_start: pd.Timestamp = REQUIRED_HISTORY_START,
    required_available_start: pd.Timestamp = REQUIRED_AVAILABLE_START,
    required_rebalance_start: pd.Timestamp = REQUIRED_REBALANCE_START,
    min_factor_ready_codes_per_rebalance: int = MIN_FACTOR_READY_CODES_PER_REBALANCE,
) -> dict[str, Any]:
    provenance_contract = validate_pit_contract(candidate, pit_panel, required_value_columns=())
    hml_cma_contract = _factor_contract(candidate, pit_panel, ("bps", "assets"))
    profitability_contract = _factor_contract(candidate, pit_panel, ("book_equity", "operating_income"))
    coverage = assess_universe_coverage(
        candidate,
        min_universe_codes=min_universe_codes,
        min_hml_cma_ready_row_share=min_hml_cma_ready_row_share,
        min_profitability_ready_row_share=min_profitability_ready_row_share,
        required_history_start=required_history_start,
        required_available_start=required_available_start,
    )
    asof_coverage = assess_asof_rebalance_coverage(
        candidate,
        pit_panel,
        required_rebalance_start=required_rebalance_start,
        min_factor_ready_codes=min_factor_ready_codes_per_rebalance,
    )
    true_pit_source = source == "opendart_receipt_xbrl"
    hml_cma_ready = bool(
        provenance_contract.eligible
        and hml_cma_contract.eligible
        and coverage["hml_cma"]["passed"]
        and asof_coverage["hml_cma"]["passed"]
        and true_pit_source
    )
    profitability_ready = bool(
        provenance_contract.eligible
        and profitability_contract.eligible
        and coverage["profitability"]["passed"]
        and asof_coverage["profitability"]["passed"]
        and true_pit_source
    )
    input_ready = bool(hml_cma_ready and profitability_ready)
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "factor_true_pit_readiness_gate",
        "current_input": {
            "source": source,
            "rows": int(len(candidate)),
            "codes": int(candidate["code"].astype(str).str.zfill(6).nunique()) if "code" in candidate.columns else 0,
            "receipt_versioned": true_pit_source,
        },
        "opendart_collection": {
            "api_key_present": bool(opendart_key_present),
            "required_snapshot_semantics": "original_filing_or_revision_version_available_at_that_time",
            "single_account_latest_view_is_not_revision_safe": True,
        },
        "contract": hml_cma_contract.to_dict(),
        "contracts": {
            "provenance": provenance_contract.to_dict(),
            "hml_cma": hml_cma_contract.to_dict(),
            "profitability": profitability_contract.to_dict(),
        },
        "universe_coverage": coverage,
        "asof_rebalance_coverage": asof_coverage,
        "promotion": {
            "status": "TRUE_PIT_INPUTS_READY" if input_ready else "BLOCKED_TRUE_PIT_INPUTS",
            "hml_cma_live_promotion_allowed": False,
            "profitability_live_promotion_allowed": False,
            "hml_cma_backtest_allowed": hml_cma_ready,
            "profitability_backtest_allowed": profitability_ready,
            "factor_backtest_allowed": input_ready,
            "reason": (
                "inputs_ready_but_strategy_requires_new_independent_oos_backtest"
                if input_ready
                else "true_pit_contract_or_source_not_satisfied"
            ),
        },
        "next_requirements": (
            [
                "rerun HML/CMA with asset-growth CMA plus profitability proxy using as-of revision-aware snapshots and the historical PIT universe",
                "use t-to-next-session execution and 31/50/75bp cost stress",
                "keep strategy research-only until independent OOS evidence passes",
            ]
            if input_ready
            else [
                "collect/build receipt-versioned OpenDART XBRL fundamentals with actual available_at dates",
                "resolve missing or ambiguous BPS/assets/operating-income facts without unsafe fallbacks",
                "expand to at least 100 domestic-equity codes with filing history starting by 2021-03-31 before full factor backtest",
                "retain filing receipt identifiers and amendment versions",
                "intersect every future rebalance snapshot with the historical PIT universe",
            ]
        ),
    }


def load_preferred_candidate(
    *,
    opendart_path: Path = OPENDART_PIT_CSV,
    naver_path: Path = NAVER_CSV,
) -> tuple[pd.DataFrame, str]:
    """Load receipt-versioned OpenDART data when present, otherwise Naver diagnostic data."""
    if opendart_path.exists():
        frame = pd.read_csv(
            opendart_path,
            dtype={"code": str, "rcept_no": str, "reprt_code": str},
        )
        return frame, "opendart_receipt_xbrl"
    if not naver_path.exists():
        raise FileNotFoundError(f"neither OpenDART PIT nor Naver fundamentals exist: {opendart_path}, {naver_path}")
    naver = pd.read_csv(naver_path, dtype={"code": str})
    return naver_current_view_candidate(naver), "naver_current_view"


def main() -> int:
    if not PIT_PANEL_CSV.exists():
        raise FileNotFoundError(PIT_PANEL_CSV)

    candidate, source = load_preferred_candidate()
    pit_panel = pd.read_csv(PIT_PANEL_CSV, dtype={"code": str})
    report = build_readiness_report(
        candidate,
        pit_panel,
        source=source,
        opendart_key_present=bool(os.getenv("OPENDART_API_KEY")),
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    hml_cma_contract = report["contracts"]["hml_cma"]
    profitability_contract = report["contracts"]["profitability"]
    print(f"factor_pit_source={source}")
    print(f"factor_pit_status={hml_cma_contract['status']}")
    print(f"eligible={hml_cma_contract['eligible']}")
    print(f"hml_cma_backtest_allowed={report['promotion']['hml_cma_backtest_allowed']}")
    print(f"profitability_backtest_allowed={report['promotion']['profitability_backtest_allowed']}")
    print(f"factor_backtest_allowed={report['promotion']['factor_backtest_allowed']}")
    print(f"universe_coverage_passed={report['universe_coverage']['combined']['passed']}")
    print(f"universe_coverage_reasons={','.join(report['universe_coverage']['combined']['reasons'])}")
    print(f"asof_rebalance_coverage_passed={report['asof_rebalance_coverage']['passed']}")
    print(f"asof_rebalance_coverage_reasons={','.join(report['asof_rebalance_coverage']['reasons'])}")
    print(f"hml_cma_reasons={','.join(hml_cma_contract['reasons'])}")
    print(f"profitability_reasons={','.join(profitability_contract['reasons'])}")
    print(f"opendart_api_key_present={report['opendart_collection']['api_key_present']}")
    print(f"output={OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
