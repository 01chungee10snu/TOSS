"""Build the practical universe and store all artifacts in Google Sheets.

No local CSVs for large datasets — everything goes to a dedicated spreadsheet
with multiple tabs to avoid eating local disk space.

Tabs:
  - universe: code list with name, sector, market cap, liquidity rank
  - panel_metadata: summary stats (row count, date range, etc.)
  - build_status: run config + error log

The actual OHLCV panel (400 codes × ~1100 days = ~440k rows) is stored as a
compressed Parquet at a SMALL footprint (~5-10MB) OR uploaded to Drive as a
file attachment. Sheets itself has a 10M cell limit and is not ideal for
raw OHLCV, so we keep the panel as a Drive file and store metadata in Sheets.

Usage:
    python scripts/build_practical_universe_panel.py
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import FinanceDataReader as fdr
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from toss_alpha.daily.universe import UniverseConfig, build_practical_universe

GAPI = f"python {Path.home()}/.hermes/skills/productivity/google-workspace/scripts/google_api.py"
DRIVE_PARQUET = ROOT / "reports/backtests/practical_universe_panel.parquet"
DRIVE_PARQUET.parent.mkdir(parents=True, exist_ok=True)

START = "2022-01-01"
END = "2026-06-21"
SPREADSHEET_TITLE = "TOSS Practical Universe"
UNIVERSE_SIZE = 400
FORCE_INCLUDE_TOP_N = 80
SECTOR_CAP_PCT = 0.25


def now_kst() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def gapi(*args: str) -> dict:
    """Run google_api.py and return parsed JSON."""
    import subprocess
    cmd = f"{GAPI} " + " ".join(args)
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        return {"error": result.stderr.strip(), "stdout": result.stdout.strip()}
    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {"raw": result.stdout.strip()}


def gapi_sheets_find_or_create(title: str) -> str:
    """Find existing spreadsheet by title, or create new. Returns sheet_id."""
    results = gapi(f"drive search '{title}' --max 5")
    if isinstance(results, list):
        for item in results:
            if item.get("name") == title and "spreadsheet" in item.get("mimeType", ""):
                print(f"  Found existing sheet: {item['id']}")
                return item["id"]
    # Create new
    result = gapi(f"sheets create --title '{title}'")
    sheet_id = result.get("spreadsheetId", "")
    print(f"  Created new sheet: {sheet_id}")
    return sheet_id


def yf_suffix(row: pd.Series) -> str:
    market_id = str(row.get("MarketId", ""))
    market = str(row.get("Market", ""))
    if market_id == "KSQ" or market == "KOSDAQ":
        return ".KQ"
    return ".KS"


def download_one(args: tuple[str, str]) -> tuple[str, pd.DataFrame | None]:
    code, ticker = args
    try:
        df = yf.download(ticker, start=START, end=END, progress=False, auto_adjust=False)
        if df is None or df.empty:
            return code, None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.reset_index()
        df["code"] = code
        return code, df
    except Exception as exc:
        print(f"  ERROR {code} ({ticker}): {exc}", flush=True)
        return code, None


def main() -> None:
    print(f"[{now_kst()}] === Practical Universe Build (Google Sheets storage) ===")

    # 1. Fetch KRX listing
    print(f"[{now_kst()}] Fetching KRX listing...")
    listing = fdr.StockListing("KRX")
    listing["Code"] = listing["Code"].astype(str).str.zfill(6)
    listing["YFTicker"] = listing.apply(lambda r: r["Code"] + yf_suffix(r), axis=1)
    print(f"  KRX listing: {len(listing)} rows")

    # 2. Build universe
    cfg = UniverseConfig(
        size=UNIVERSE_SIZE,
        sector_cap_pct=SECTOR_CAP_PCT,
        force_include_top_n=FORCE_INCLUDE_TOP_N,
    )
    universe_codes = build_practical_universe(listing, config=cfg)
    print(f"  Practical universe: {len(universe_codes)} codes")

    # Verify key large caps
    must_have = {"005930": "삼성전자", "000660": "SK하이닉스", "035420": "NAVER"}
    for code, name in must_have.items():
        status = "✓" if code in universe_codes else "✗ MISSING"
        print(f"    {code} {name}: {status}")

    # 3. Build universe dataframe with metadata
    universe_rows = []
    for rank, code in enumerate(universe_codes, 1):
        row = listing[listing["Code"] == code]
        if not row.empty:
            r = row.iloc[0]
            universe_rows.append({
                "rank": rank,
                "code": code,
                "name": r.get("Name", ""),
                "market": r.get("Market", ""),
                "sector": r.get("Sector", ""),
                "marcap": r.get("Marcap", ""),
                "yf_ticker": r.get("YFTicker", ""),
            })
    universe_df = pd.DataFrame(universe_rows)

    # 4. Download OHLCV panel
    tickers = []
    for code in universe_codes:
        row = listing[listing["Code"] == code]
        if not row.empty:
            tickers.append((code, row.iloc[0]["YFTicker"]))

    print(f"[{now_kst()}] Downloading {len(tickers)} tickers...")
    all_frames: list[pd.DataFrame] = []
    errors: list[str] = []
    done = 0
    with ProcessPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(download_one, t): t[0] for t in tickers}
        for future in as_completed(futures):
            code, df = future.result()
            done += 1
            if df is not None and len(df) > 0:
                all_frames.append(df)
            else:
                errors.append(code)
            if done % 50 == 0:
                print(f"    [{done}/{len(tickers)}] frames={len(all_frames)} errors={len(errors)}", flush=True)

    print(f"[{now_kst()}] Download complete. frames={len(all_frames)}, errors={len(errors)}")

    # 5. Save panel as Parquet (compact), upload to Drive
    if all_frames:
        panel = pd.concat(all_frames, ignore_index=True)
        panel["code"] = panel["code"].astype(str).str.zfill(6)
        cols = ["Date", "code", "Open", "High", "Low", "Close", "Adj Close", "Volume"]
        panel = panel[[c for c in cols if c in panel.columns]].copy()
        panel = panel.sort_values(["code", "Date"]).reset_index(drop=True)
        panel.to_parquet(DRIVE_PARQUET, compression="zstd")
        size_mb = DRIVE_PARQUET.stat().st_size / 1024 / 1024
        print(f"  Panel Parquet: {DRIVE_PARQUET} ({size_mb:.1f}MB)")
        print(f"  Rows: {len(panel):,}  Codes: {panel['code'].nunique()}")
        print(f"  Date range: {panel['Date'].min()} ~ {panel['Date'].max()}")

        # Upload to Drive
        print(f"[{now_kst()}] Uploading panel to Google Drive...")
        upload_result = gapi(f"drive upload '{DRIVE_PARQUET}' --name 'practical_universe_panel.parquet'")
        print(f"  Upload: {upload_result.get('status', 'unknown')} id={upload_result.get('id', '')}")
        drive_file_id = upload_result.get("id", "")
        drive_link = upload_result.get("webViewLink", "")
    else:
        print("  ERROR: no frames downloaded")
        drive_file_id = ""
        drive_link = ""

    # 6. Find or create Google Sheet
    print(f"[{now_kst()}] Setting up Google Sheet '{SPREADSHEET_TITLE}'...")
    sheet_id = gapi_sheets_find_or_create(SPREADSHEET_TITLE)

    # 7. Write universe tab
    print(f"[{now_kst()}] Writing universe tab ({len(universe_df)} rows)...")
    header = [["rank", "code", "name", "market", "sector", "marcap", "yf_ticker"]]
    data_rows = universe_df.values.tolist()
    # Batch in chunks of 100 rows (Sheets API limit safety)
    all_values = header + data_rows
    # Write first 100 rows with update, rest with append
    if len(all_values) <= 100:
        values_str = json.dumps(all_values, ensure_ascii=False)
        gapi(f'sheets update {sheet_id} "universe!A1" --values \'{values_str}\'')
    else:
        first_batch = json.dumps(all_values[:100], ensure_ascii=False)
        gapi(f'sheets update {sheet_id} "universe!A1" --values \'{first_batch}\'')
        remaining = all_values[100:]
        chunk_size = 100
        for i in range(0, len(remaining), chunk_size):
            chunk = json.dumps(remaining[i:i + chunk_size], ensure_ascii=False)
            gapi(f'sheets append {sheet_id} "universe!A:G" --values \'{chunk}\'')

    # 8. Write build_status tab
    status_data = [
        ["key", "value"],
        ["run_ts", now_kst()],
        ["universe_size", str(len(universe_codes))],
        ["frames_downloaded", str(len(all_frames))],
        ["error_count", str(len(errors))],
        ["errors", ",".join(errors[:20])],
        ["config_size", str(UNIVERSE_SIZE)],
        ["force_include_top_n", str(FORCE_INCLUDE_TOP_N)],
        ["sector_cap_pct", str(SECTOR_CAP_PCT)],
        ["date_start", START],
        ["date_end", END],
        ["parquet_drive_id", drive_file_id],
        ["parquet_drive_link", drive_link],
        ["spreadsheet_id", sheet_id],
    ]
    values_str = json.dumps(status_data, ensure_ascii=False)
    gapi(f'sheets update {sheet_id} "build_status!A1" --values \'{values_str}\'')

    print(f"\n[{now_kst()}] === DONE ===")
    print(f"  Spreadsheet: https://docs.google.com/spreadsheets/d/{sheet_id}")
    print(f"  Panel on Drive: {drive_link}")
    print(f"  Local parquet (can delete): {DRIVE_PARQUET} ({DRIVE_PARQUET.stat().st_size / 1024 / 1024:.1f}MB)")


if __name__ == "__main__":
    main()
