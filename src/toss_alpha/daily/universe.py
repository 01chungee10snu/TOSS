"""Practical tradability universe builder.

Constructs a liquid, tradeable KOSPI+KOSDAQ universe that:
- includes large-cap leaders (no blanket exclusions),
- filters out SPACs, preferreds, managed/halted names,
- ranks by trailing dollar-volume for real deployability,
- caps per-sector concentration to avoid single-sector overload.

The output is a deterministic, reproducible code list suitable for
panel generation and backtesting.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

SECTOR_CAP_PCT = 0.25  # max 25% of universe from any single sector
DEFAULT_UNIVERSE_SIZE = 400
MIN_LOOKBACK_DAYS = 40


@dataclass
class UniverseConfig:
    size: int = DEFAULT_UNIVERSE_SIZE
    sector_cap_pct: float = SECTOR_CAP_PCT
    min_lookback_days: int = MIN_LOOKBACK_DAYS
    exclude_codes: set[str] = field(default_factory=set)
    force_include_top_n: int = 50


_SPAC_PATTERN = re.compile(r"스팩|SPAC", re.IGNORECASE)
_PREFERRED_PATTERN = re.compile(r"우$|우B$|우H$|우K$|우S$|우M$|우L$|우T$|우V$|우W$|우Y$|우Z$")


def is_tradeable(row: pd.Series) -> bool:
    """Return True if the listing row represents a tradeable common stock."""
    code = str(row.get("Code", "")).zfill(6)
    if not re.match(r"^\d{6}$", code):
        return False
    name = str(row.get("Name", ""))
    if _SPAC_PATTERN.search(name):
        return False
    if _PREFERRED_PATTERN.search(name):
        return False
    if "리츠" in name or "REIT" in name.upper():
        return False
    dept = str(row.get("Dept", "")).strip()
    if dept in {"관리", "감리", "투자경고", "거래정지"}:
        return False
    market = str(row.get("Market", "")).strip()
    if market not in {"KOSPI", "KOSDAQ"}:
        return False
    return True


def compute_dollar_volume_rank(
    panel: pd.DataFrame,
    lookback_days: int = MIN_LOOKBACK_DAYS,
) -> pd.DataFrame:
    """Rank symbols by average daily dollar volume over the trailing window."""
    panel = panel.copy()
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    panel["Date"] = pd.to_datetime(panel["Date"])
    cutoff = panel["Date"].max() - pd.Timedelta(days=lookback_days * 2)
    recent = panel[panel["Date"] >= cutoff].copy()
    if recent.empty:
        recent = panel.copy()
    recent["dollar_vol"] = recent["Close"] * recent["Volume"]
    avg = recent.groupby("code")["dollar_vol"].mean().reset_index()
    avg.columns = ["code", "avg_dollar_vol"]
    avg = avg.sort_values("avg_dollar_vol", ascending=False).reset_index(drop=True)
    avg["liquidity_rank"] = range(1, len(avg) + 1)
    return avg


def build_practical_universe(
    listing: pd.DataFrame,
    liquidity_rank: pd.DataFrame | None = None,
    config: UniverseConfig | None = None,
) -> list[str]:
    """Build a practical tradability universe from a KRX listing."""
    cfg = config or UniverseConfig()
    tradeable = listing[listing.apply(is_tradeable, axis=1)].copy()
    tradeable["Code"] = tradeable["Code"].astype(str).str.zfill(6)
    tradeable = tradeable[~tradeable["Code"].isin(cfg.exclude_codes)]

    if liquidity_rank is not None and not liquidity_rank.empty:
        merged = tradeable.merge(liquidity_rank, left_on="Code", right_on="code", how="left")
        merged = merged.dropna(subset=["avg_dollar_vol"])
        merged = merged.sort_values("avg_dollar_vol", ascending=False)
    else:
        merged = tradeable.copy()
        if "Marcap" in merged.columns:
            merged = merged.sort_values("Marcap", ascending=False)
        else:
            merged = merged.head(cfg.size)

    # Force-include top-N by market cap.
    force_codes: set[str] = set()
    if "Marcap" in merged.columns and cfg.force_include_top_n > 0:
        top_cap = merged.nlargest(cfg.force_include_top_n, "Marcap")
        force_codes = set(top_cap["Code"].tolist())

    selected: list[str] = []
    sector_counts: dict[str, int] = {}
    cap = max(1, int(cfg.size * cfg.sector_cap_pct))
    sector_col = "Sector" if "Sector" in merged.columns else None

    # Pass 1: respect sector cap.
    deferred: list[pd.Series] = []
    for _, row in merged.iterrows():
        code = str(row["Code"]).zfill(6)
        if code in selected:
            continue
        sector = str(row.get(sector_col, "Unknown")) if sector_col else "Unknown"
        if code in force_codes:
            selected.append(code)
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
            continue
        if sector_counts.get(sector, 0) >= cap:
            deferred.append(row)
            continue
        selected.append(code)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if len(selected) >= cfg.size:
            break

    # Pass 2: fill remaining slots from deferred (cap relaxed).
    if len(selected) < cfg.size:
        for row in deferred:
            if len(selected) >= cfg.size:
                break
            code = str(row["Code"]).zfill(6)
            if code not in selected:
                selected.append(code)

    return sorted(selected)
