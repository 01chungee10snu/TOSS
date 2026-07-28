#!/usr/bin/env python3
"""Incrementally update the practical_universe_400 OHLCV panel with recent yfinance data.

This mirrors update_random500_panel_2026.py but targets the practical universe panel
used by the live trading loop (TOSS_PANEL_CSV).

Workflow:
1. Load the base panel CSV (practical_universe_400_*_latest_ohlcv_panel.csv)
2. Determine which codes need KOSDAQ (.KQ) vs KOSPI (.KS) suffix
3. Download 2026 YTD data via yfinance (batch, single-threaded)
4. Merge new rows onto the base panel
5. Write updated CSV back to the same path

Usage:
    python scripts/update_practical_universe_panel_2026.py
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
BACKTEST_DIR = ROOT / "reports" / "backtests"
PANEL_CSV = BACKTEST_DIR / "practical_universe_400_2022-01-01_2026-latest_ohlcv_panel.csv"
STATUS_JSON = BACKTEST_DIR / "practical_universe_400_2026_update_status.json"
START_2026 = "2026-01-01"
YF_END = (date.today() + timedelta(days=1)).isoformat()


def _download_one(args: tuple[str, str]) -> tuple[str, pd.DataFrame | None]:
    code, ticker = args
    try:
        df = yf.download(ticker, start=START_2026, end=YF_END, progress=False, auto_adjust=True, threads=False)
        if df is None or df.empty:
            return code, None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df = df.reset_index()
        df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col not in df.columns:
                df[col] = pd.NA
        df["code"] = code
        return code, df[["Date", "Open", "High", "Low", "Close", "Volume", "code"]]
    except Exception:
        return code, None


def _resolve_ticker_suffix(panel: pd.DataFrame) -> dict[str, str]:
    """Determine .KS vs .KQ for each code by probing yfinance."""
    codes = panel["code"].astype(str).str.zfill(6).unique()
    suffix_map: dict[str, str] = {}
    # Test in small batches — try .KS first (covers most), fallback .KQ
    test_start = (date.today() - timedelta(days=7)).isoformat()
    test_end = YF_END
    for code in codes:
        for suffix in [".KS", ".KQ"]:
            ticker = f"{code}{suffix}"
            try:
                df = yf.download(ticker, start=test_start, end=test_end, progress=False, auto_adjust=True, threads=False)
                if df is not None and not df.empty:
                    suffix_map[code] = suffix
                    break
            except Exception:
                pass
        else:
            suffix_map[code] = ".KS"  # default
        time.sleep(0.02)
    return suffix_map


def main() -> int:
    if not PANEL_CSV.exists():
        print(f"ERROR: panel CSV not found: {PANEL_CSV}", file=sys.stderr)
        return 1

    print(f"[1/4] Loading base panel: {PANEL_CSV}")
    base = pd.read_csv(PANEL_CSV, dtype={"code": str}, parse_dates=["Date"])
    base["code"] = base["code"].astype(str).str.zfill(6)
    base_latest = base["Date"].max().date()
    print(f"  Base rows: {len(base)}, codes: {base['code'].nunique()}, latest: {base_latest}")

    # Get name mapping from panel
    name_map = base.groupby("code")["name"].first().to_dict()

    print(f"[2/4] Resolving yfinance ticker suffixes (400 codes)...")
    suffix_map = _resolve_ticker_suffix(base)
    ks_count = sum(1 for v in suffix_map.values() if v == ".KS")
    kq_count = sum(1 for v in suffix_map.values() if v == ".KQ")
    print(f"  KOSPI(.KS): {ks_count}, KOSDAQ(.KQ): {kq_count}")

    tasks = [(code, f"{code}{suffix_map[code]}") for code in base["code"].unique()]
    print(f"[3/4] Downloading {len(tasks)} codes from yfinance ({START_2026} to {YF_END})...")

    results: dict[str, pd.DataFrame | None] = {}
    completed = 0
    # Use process pool for parallel downloads
    with ProcessPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_download_one, task): task[0] for task in tasks}
        for future in as_completed(futures):
            code = futures[future]
            try:
                _, df = future.result()
            except Exception:
                df = None
            results[code] = df
            completed += 1
            if completed % 50 == 0:
                print(f"  [{completed}/{len(tasks)}] processed", flush=True)

    # Merge
    frames = []
    success = 0
    no_data = 0
    for code, df in results.items():
        if df is not None and not df.empty:
            df["name"] = name_map.get(code, code)
            frames.append(df)
            success += 1
        else:
            no_data += 1
    print(f"  Downloaded: {success}, no_data: {no_data}")

    if not frames:
        print("ERROR: no data downloaded", file=sys.stderr)
        return 1

    add = pd.concat(frames, ignore_index=True)
    # Filter to rows after base_latest to avoid duplicates
    add = add[add["Date"] > pd.Timestamp(base_latest)]
    print(f"  New rows to append: {len(add)}")

    if len(add) == 0:
        print("Panel already up to date.")
        STATUS_JSON.write_text('{"status":"UP_TO_DATE","latest":"' + str(base_latest) + '"}')
        return 0

    # Merge: keep only needed columns order
    merged = pd.concat([base, add[list(base.columns)]], ignore_index=True)
    merged = merged.sort_values(["code", "Date"]).reset_index(drop=True)
    # Deduplicate (keep last)
    merged = merged.drop_duplicates(subset=["code", "Date"], keep="last").reset_index(drop=True)

    new_latest = merged["Date"].max().date()
    print(f"[4/4] Writing updated panel: {PANEL_CSV}")
    print(f"  Total rows: {len(merged)}, latest: {new_latest}")
    merged.to_csv(PANEL_CSV, index=False)

    STATUS_JSON.write_text(
        '{"status":"UPDATED","previous_latest":"' + str(base_latest) + '","new_latest":"' + str(new_latest) + '"}'
    )
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
