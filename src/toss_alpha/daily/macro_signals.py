"""Global macro signal collector.

Fetches daily data for key macro indicators that drive Korean equity markets:
  - VIX (volatility index) → risk appetite
  - USD/KRW exchange rate → capital flows
  - SOX (Philadelphia Semiconductor Index) → tech/semi cycle
  - US 10Y Treasury yield → discount rate
  - NASDAQ → global tech sentiment
  - Brent crude → inflation/cost pressure

All fetched via yfinance. Cached to parquet for speed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "reports/harness/macro_signals.parquet"

MACRO_TICKERS = {
    "^VIX": "vix",
    "KRW=X": "usd_krw",  # USD/KRW
    "^SOX": "sox",
    "^TNX": "us_10y_yield",  # US 10Y Treasury
    "^IXIC": "nasdaq",
    "BZ=F": "brent_crude",
}


def fetch_macro_signals(start: str = "2021-12-01", end: str = "2026-06-21") -> pd.DataFrame:
    """Fetch all macro signals and return aligned daily dataframe."""
    frames = {}
    for ticker, name in MACRO_TICKERS.items():
        try:
            df = yf.download(ticker, start=start, end=end, progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if not df.empty:
                frames[name] = df["Close"].copy()
                print(f"  {ticker} → {name}: {len(df)} rows", flush=True)
        except Exception as exc:
            print(f"  {ticker} → {name}: ERROR {exc}", flush=True)

    if not frames:
        return pd.DataFrame()

    # Align on date
    macro = pd.DataFrame(frames)
    macro.index.name = "Date"
    macro = macro.sort_index()

    # Forward-fill (macro data has holidays/weekends)
    macro = macro.ffill().dropna(how="all")

    # Derived features
    macro["vix_change_5d"] = macro["vix"].pct_change(5)
    macro["usd_krw_change_5d"] = macro["usd_krw"].pct_change(5)
    macro["sox_ret_20d"] = macro["sox"].pct_change(20)
    macro["nasdaq_ret_20d"] = macro["nasdaq"].pct_change(20)
    macro["yield_change_5d"] = macro["us_10y_yield"].diff(5)

    # Regime classification
    # risk_off: VIX > 25 OR USD/KRW surging
    # risk_on: VIX < 18 AND SOX trending up
    macro["macro_regime"] = "neutral"
    macro.loc[macro["vix"] > 25, "macro_regime"] = "risk_off"
    macro.loc[(macro["vix"] < 18) & (macro["sox_ret_20d"] > 0.02), "macro_regime"] = "risk_on"

    # Cache
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    macro.to_parquet(CACHE_PATH, compression="zstd")
    print(f"  Cached: {CACHE_PATH} ({CACHE_PATH.stat().st_size / 1024:.0f}KB)")

    return macro


def get_macro_regime(date: pd.Timestamp, macro: pd.DataFrame | None = None) -> dict:
    """Get macro regime for a given date."""
    if macro is None:
        if not CACHE_PATH.exists():
            macro = fetch_macro_signals()
        else:
            macro = pd.read_parquet(CACHE_PATH)

    # Find most recent date <= target
    mask = macro.index <= pd.Timestamp(date)
    if not mask.any():
        return {"status": "unknown", "vix": None, "usd_krw": None}

    row = macro[mask].iloc[-1]
    return {
        "status": str(row.get("macro_regime", "neutral")),
        "vix": float(row.get("vix", 0)),
        "usd_krw": float(row.get("usd_krw", 0)),
        "sox_ret_20d": float(row.get("sox_ret_20d", 0)),
        "nasdaq_ret_20d": float(row.get("nasdaq_ret_20d", 0)),
        "us_10y_yield": float(row.get("us_10y_yield", 0)),
    }


if __name__ == "__main__":
    print("=== Fetching Global Macro Signals ===")
    macro = fetch_macro_signals()
    print(f"\n{len(macro)} rows, {macro.index.min()} ~ {macro.index.max()}")
    print(f"\nRegime distribution:")
    print(macro["macro_regime"].value_counts().to_string())
    print(f"\nLatest regime:")
    latest = get_macro_regime(macro.index.max(), macro)
    for k, v in latest.items():
        print(f"  {k}: {v}")
