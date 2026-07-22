"""Build a true Point-in-Time universe panel including delisted stocks.

Fetches RAW OHLCV from FinanceDataReader for:
  1. All current KRX listings (KOSPI + KOSDAQ + KONEX)
  2. All stocks delisted during 2022-01-01 ~ 2026-07-16

Output: long-format CSV with columns:
  Date, code, name, Open, High, Low, Close, Volume, listed, delisted

Usage:
    python scripts/build_pit_universe_panel.py
"""
from __future__ import annotations

import hashlib
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import FinanceDataReader as fdr
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

START = "2022-01-01"
END = "2026-07-16"
MIN_PARALLEL = 50  # below this, fetch sequentially

OUT_CSV = ROOT / "reports" / "backtests" / "pit_full_universe_2022-01-01_2026_ohlcv_panel.csv"
STATUS_JSON = ROOT / "reports" / "backtests" / "pit_panel_build_status.json"

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Build the PIT universe manifest
# ─────────────────────────────────────────────────────────────────────────────

def build_universe_manifest() -> pd.DataFrame:
    """Return DataFrame with all stocks that were listed at any point in our period."""
    # Current listings
    current = fdr.StockListing("KRX")
    current = current[["Code", "Name", "Market"]].copy()
    current.rename(columns={"Code": "code", "Name": "name", "Market": "market"}, inplace=True)
    current["listed"] = pd.NaT
    current["delisted"] = pd.NaT
    current["status"] = "active"
    print(f"  Current KRX listings: {len(current)}")

    # Delisted during our period
    dl = fdr.StockListing("KRX-DELISTING")
    dl = dl[dl["SecuGroup"] == "주권"].copy()
    dl["DelistingDate"] = pd.to_datetime(dl["DelistingDate"])
    dl["ListingDate"] = pd.to_datetime(dl["ListingDate"])
    mask = (dl["DelistingDate"] >= START) & (dl["DelistingDate"] <= END)
    dl = dl[mask]
    dl = dl[["Symbol", "Name", "Market", "ListingDate", "DelistingDate"]].copy()
    dl.rename(columns={
        "Symbol": "code", "Name": "name", "Market": "market",
        "ListingDate": "listed", "DelistingDate": "delisted",
    }, inplace=True)
    dl["status"] = "delisted"
    print(f"  Delisted in period: {len(dl)}")

    manifest = pd.concat([current, dl], ignore_index=True)
    # Deduplicate by code (prefer delisted entry for richer metadata)
    manifest = manifest.drop_duplicates(subset="code", keep="last")
    # Filter out non-6-digit codes (preferred shares, rights, etc.)
    manifest = manifest[manifest["code"].str.match(r"^\d{6}$", na=False)]
    manifest = manifest.reset_index(drop=True)
    print(f"  Total PIT universe: {len(manifest)}")
    return manifest


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Fetch OHLCV for each stock (parallel)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_one(args: tuple[str, str, str]) -> pd.DataFrame | None:
    """Fetch raw OHLCV for a single stock. Returns long-format DataFrame or None."""
    code, name, market = args
    try:
        df = fdr.DataReader(code, START, END)
        if df is None or len(df) == 0:
            return None
        df = df.reset_index()
        df = df.rename(columns={"Date": "date"})
        df["code"] = code
        df["name"] = name
        df["market"] = market
        # Keep only OHLCV columns
        cols = ["date", "code", "name", "market", "Open", "High", "Low", "Close", "Volume"]
        df = df[[c for c in cols if c in df.columns]]
        return df
    except Exception:
        return None


def fetch_all_parallel(manifest: pd.DataFrame) -> pd.DataFrame:
    """Fetch OHLCV for all stocks in parallel using ProcessPoolExecutor."""
    tasks = list(zip(manifest["code"], manifest["name"], manifest["market"]))
    results: list[pd.DataFrame] = []
    done = 0
    total = len(tasks)
    started = time.time()

    # Use ProcessPoolExecutor for COW parallelism on macOS
    n_workers = min(os.cpu_count() or 8, 20)
    print(f"  Thread workers: {n_workers}")

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(fetch_one, t): t[0] for t in tasks}
        for future in as_completed(futures):
            done += 1
            df = future.result()
            if df is not None and len(df) > 0:
                results.append(df)
            if done % 200 == 0 or done == total:
                elapsed = time.time() - started
                rate = done / max(elapsed, 0.001)
                eta = (total - done) / max(rate, 0.001)
                print(
                    f"  [{done}/{total}] fetched={len(results)} "
                    f"rate={rate:.1f}/s eta={eta:.0f}s"
                )

    if not results:
        raise RuntimeError("No OHLCV data fetched")
    panel = pd.concat(results, ignore_index=True)
    return panel


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Post-process and save
# ─────────────────────────────────────────────────────────────────────────────

def post_process(panel: pd.DataFrame, manifest: pd.DataFrame) -> pd.DataFrame:
    """Clean and merge with manifest metadata."""
    # Ensure correct dtypes
    panel["date"] = pd.to_datetime(panel["date"])
    for col in ["Open", "High", "Low", "Close"]:
        panel[col] = pd.to_numeric(panel[col], errors="coerce")
    panel["Volume"] = pd.to_numeric(panel["Volume"], errors="coerce")

    # Merge delisting dates
    meta = manifest[["code", "listed", "delisted", "status"]].copy()
    panel = panel.merge(meta, on="code", how="left")

    # Sort
    panel = panel.sort_values(["code", "date"]).reset_index(drop=True)

    # Filter: remove rows after delisting date
    if "delisted" in panel.columns:
        valid = panel["delisted"].isna() | (panel["date"] <= panel["delisted"])
        before = len(panel)
        panel = panel[valid]
        after = len(panel)
        if before != after:
            print(f"  Removed {before - after} rows after delisting dates")

    return panel


def main():
    print("=" * 70)
    print("PIT Universe Panel Builder")
    print(f"Period: {START} ~ {END}")
    print("=" * 70)

    # Step 1: Build manifest
    print("\n[1/4] Building PIT universe manifest...")
    manifest = build_universe_manifest()

    # Step 2: Fetch all OHLCV
    print(f"\n[2/4] Fetching OHLCV for {len(manifest)} stocks...")
    panel = fetch_all_parallel(manifest)
    print(f"  Raw panel: {len(panel):,} rows, {panel['code'].nunique()} stocks")

    # Step 3: Post-process
    print("\n[3/4] Post-processing...")
    panel = post_process(panel, manifest)

    # Step 4: Save
    print(f"\n[4/4] Saving to {OUT_CSV}...")
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    # Stats
    stats = {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "period": f"{START} ~ {END}",
        "total_stocks": int(panel["code"].nunique()),
        "total_rows": int(len(panel)),
        "delisted_stocks": int(manifest["status"].eq("delisted").sum()),
        "active_stocks": int(manifest["status"].eq("active").sum()),
        "date_range": f"{panel['date'].min().date()} ~ {panel['date'].max().date()}",
        "file": str(OUT_CSV),
        "file_size_mb": round(OUT_CSV.stat().st_size / 1024 / 1024, 1),
    }
    STATUS_JSON.parent.mkdir(parents=True, exist_ok=True)
    STATUS_JSON.write_text(
        __import__("json").dumps(stats, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\n" + "=" * 70)
    print("DONE")
    print(f"  Stocks: {stats['total_stocks']:,}")
    print(f"  Rows: {stats['total_rows']:,}")
    print(f"  Delisted: {stats['delisted_stocks']}")
    print(f"  Active: {stats['active_stocks']}")
    print(f"  File: {OUT_CSV}")
    print(f"  Size: {stats['file_size_mb']} MB")
    print("=" * 70)


if __name__ == "__main__":
    main()
