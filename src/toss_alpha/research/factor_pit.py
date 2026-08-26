"""Point-in-time evidence contract for fundamental factor research.

A high backtest return is not enough to call a factor strategy PIT-valid.  The
fundamental values must be tied to the date they were actually available, the
historical revision shown to the market at that time, and a tradeable universe
that contains delisted names.  This module centralizes those checks so research
reports fail closed instead of silently promoting current-view data.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

import pandas as pd

REQUIRED_COLUMNS = {
    "code",
    "period_end",
    "available_at",
    "source",
    "is_estimate",
    "revision_safe",
}


@dataclass(frozen=True)
class PitContractResult:
    status: str
    eligible: bool
    reasons: tuple[str, ...]
    stats: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _bool_series(series: pd.Series, *, default: bool = False) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(default)
    truthy = {"1", "true", "yes", "y", "on"}
    return series.fillna(default).astype(str).str.strip().str.lower().isin(truthy)


def normalize_fundamentals(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize a PIT-fundamental frame without inventing missing evidence."""
    missing = sorted(REQUIRED_COLUMNS.difference(frame.columns))
    if missing:
        raise ValueError(f"missing PIT fundamental columns: {','.join(missing)}")

    result = frame.copy()
    result["code"] = result["code"].astype(str).str.strip().str.zfill(6)
    result["period_end"] = pd.to_datetime(result["period_end"], errors="coerce")
    result["available_at"] = pd.to_datetime(result["available_at"], errors="coerce")
    result["source"] = result["source"].fillna("").astype(str).str.strip()
    result["is_estimate"] = _bool_series(result["is_estimate"])
    result["revision_safe"] = _bool_series(result["revision_safe"])
    return result


def normalize_universe_panel(panel: pd.DataFrame) -> pd.DataFrame:
    """Normalize an OHLCV universe panel used to prove historical tradability."""
    if "code" not in panel.columns:
        raise ValueError("historical universe panel missing code")
    date_col = "date" if "date" in panel.columns else "Date" if "Date" in panel.columns else None
    if date_col is None:
        raise ValueError("historical universe panel missing date/Date")
    result = panel.copy()
    result["code"] = result["code"].astype(str).str.strip().str.zfill(6)
    result["_pit_date"] = pd.to_datetime(result[date_col], errors="coerce").dt.normalize()
    return result


def validate_pit_contract(
    fundamentals: pd.DataFrame,
    universe_panel: pd.DataFrame,
    *,
    required_value_columns: Iterable[str] = ("bps", "revenue"),
) -> PitContractResult:
    """Return whether inputs are strong enough for a true-PIT factor backtest."""
    reasons: list[str] = []
    stats: dict[str, Any] = {}

    try:
        fund = normalize_fundamentals(fundamentals)
    except ValueError as exc:
        return PitContractResult("BLOCKED_PIT_CONTRACT", False, (str(exc),), {"rows": len(fundamentals)})

    missing_values = sorted(set(required_value_columns).difference(fund.columns))
    if missing_values:
        reasons.append(f"missing_factor_values:{','.join(missing_values)}")

    missing_period = int(fund["period_end"].isna().sum())
    missing_available = int(fund["available_at"].isna().sum())
    estimate_rows = int(fund["is_estimate"].sum())
    unsafe_rows = int((~fund["revision_safe"]).sum())
    source_missing = int(fund["source"].eq("").sum())
    impossible_availability = int(
        ((fund["available_at"].notna()) & (fund["period_end"].notna()) & (fund["available_at"] < fund["period_end"])).sum()
    )

    stats.update(
        {
            "rows": int(len(fund)),
            "codes": int(fund["code"].nunique()),
            "missing_period_end_rows": missing_period,
            "missing_available_at_rows": missing_available,
            "estimate_rows": estimate_rows,
            "revision_unsafe_rows": unsafe_rows,
            "missing_source_rows": source_missing,
            "availability_before_period_end_rows": impossible_availability,
        }
    )

    if missing_period:
        reasons.append("period_end_missing")
    if missing_available:
        reasons.append("actual_filing_availability_missing")
    if estimate_rows:
        reasons.append("estimate_rows_present")
    if unsafe_rows:
        reasons.append("revision_safe_historical_values_missing")
    if source_missing:
        reasons.append("source_provenance_missing")
    if impossible_availability:
        reasons.append("availability_precedes_period_end")

    try:
        panel = normalize_universe_panel(universe_panel)
    except ValueError as exc:
        reasons.append(str(exc))
        panel = pd.DataFrame()

    if not panel.empty:
        has_delisting_metadata = "delisted" in panel.columns
        delisted_rows = int(panel["delisted"].notna().sum()) if has_delisting_metadata else 0
        delisted_codes = int(panel.loc[panel["delisted"].notna(), "code"].nunique()) if has_delisting_metadata else 0
        stats.update(
            {
                "universe_rows": int(len(panel)),
                "universe_codes": int(panel["code"].nunique()),
                "universe_start": str(panel["_pit_date"].min().date()) if panel["_pit_date"].notna().any() else None,
                "universe_end": str(panel["_pit_date"].max().date()) if panel["_pit_date"].notna().any() else None,
                "has_delisting_metadata": has_delisting_metadata,
                "delisted_rows": delisted_rows,
                "delisted_codes": delisted_codes,
            }
        )
        if not has_delisting_metadata:
            reasons.append("historical_universe_delisting_metadata_missing")
        elif delisted_codes <= 0:
            reasons.append("historical_universe_contains_no_delisted_names")

    eligible = not reasons
    return PitContractResult(
        status="TRUE_PIT_ELIGIBLE" if eligible else "BLOCKED_PIT_CONTRACT",
        eligible=eligible,
        reasons=tuple(dict.fromkeys(reasons)),
        stats=stats,
    )


def pit_snapshot(fundamentals: pd.DataFrame, as_of: Any, *, require_revision_safe: bool = True) -> pd.DataFrame:
    """Return each code's latest genuinely available fundamental period as of a date."""
    fund = normalize_fundamentals(fundamentals)
    cutoff = pd.Timestamp(as_of)
    eligible = fund[
        fund["available_at"].notna()
        & fund["period_end"].notna()
        & (fund["available_at"] <= cutoff)
        & (~fund["is_estimate"])
    ].copy()
    if require_revision_safe:
        eligible = eligible[eligible["revision_safe"]]
    if eligible.empty:
        return eligible

    # Multiple filings/amendments for one period are allowed if the dataset is
    # revision-safe.  Keep the latest version available by the cutoff, then the
    # newest reporting period for each code.
    eligible = eligible.sort_values(["code", "period_end", "available_at"])
    per_period = eligible.groupby(["code", "period_end"], as_index=False).tail(1)
    return per_period.sort_values(["code", "period_end", "available_at"]).groupby("code", as_index=False).tail(1).reset_index(drop=True)


def tradable_codes_on(universe_panel: pd.DataFrame, as_of: Any) -> set[str]:
    """Return codes with an actual market row on the requested trading date."""
    panel = normalize_universe_panel(universe_panel)
    target = pd.Timestamp(as_of).normalize()
    return set(panel.loc[panel["_pit_date"] == target, "code"].astype(str))


def intersect_snapshot_with_universe(snapshot: pd.DataFrame, universe_panel: pd.DataFrame, as_of: Any) -> pd.DataFrame:
    """Restrict a fundamental snapshot to names actually tradeable on that date."""
    allowed = tradable_codes_on(universe_panel, as_of)
    if not allowed or snapshot.empty:
        return snapshot.iloc[0:0].copy()
    result = snapshot.copy()
    result["code"] = result["code"].astype(str).str.zfill(6)
    return result[result["code"].isin(allowed)].reset_index(drop=True)


def naver_current_view_candidate(frame: pd.DataFrame) -> pd.DataFrame:
    """Map the existing Naver research extract into the PIT schema, fail-closed.

    ``available_at`` uses the script's conservative assumed signal date when
    present, but ``revision_safe`` is always False because a 2026 current-view
    page cannot prove what value was visible at an earlier filing date.
    """
    result = frame.copy()
    result["code"] = result["code"].astype(str).str.zfill(6)
    year = pd.to_numeric(result.get("year"), errors="coerce")
    month = pd.to_numeric(result.get("month"), errors="coerce")
    result["period_end"] = [
        pd.Timestamp(year=int(y), month=int(m), day=1) + pd.offsets.MonthEnd(1)
        if pd.notna(y) and pd.notna(m)
        else pd.NaT
        for y, m in zip(year, month)
    ]
    if "signal_date" in result.columns:
        result["available_at"] = pd.to_datetime(result["signal_date"], errors="coerce")
    else:
        result["available_at"] = pd.NaT
    if "is_estimate" not in result.columns:
        period = result.get("period", pd.Series("", index=result.index)).astype(str)
        result["is_estimate"] = period.str.contains(r"\(E\)", regex=True, na=False)
    result["revision_safe"] = False
    result["source"] = "naver_current_view"
    if "bps" not in result.columns and "BPS" in result.columns:
        result["bps"] = result["BPS"]
    return result
