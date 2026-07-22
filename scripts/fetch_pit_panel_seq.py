#!/usr/bin/env python3
"""Fetch PIT universe OHLCV — sequential with incremental save and retry."""
import time, json, sys, signal
from pathlib import Path
import pandas as pd
import FinanceDataReader as fdr

ROOT = Path(__file__).resolve().parents[1]
START, END = "2022-01-01", "2026-07-16"
OUT = ROOT / "reports/backtests/pit_full_universe_2022-01-01_2026_ohlcv_panel.csv"
CHECKPOINT = ROOT / "reports/backtests/pit_panel_checkpoint.parquet"

def main():
    # Build universe
    print("Building universe...", flush=True)
    current = fdr.StockListing("KRX")
    current_map = dict(zip(current["Code"], current["Name"]))
    dl = fdr.StockListing("KRX-DELISTING")
    dl = dl[dl["SecuGroup"] == "주권"].copy()
    dl["DelistingDate"] = pd.to_datetime(dl["DelistingDate"])
    mask = (dl["DelistingDate"] >= START) & (dl["DelistingDate"] <= END)
    dl = dl[mask]
    dl_map = dict(zip(dl["Symbol"], dl["Name"]))
    dl_dates = dict(zip(dl["Symbol"], dl["DelistingDate"]))
    all_codes = sorted(set(current["Code"]) | set(dl["Symbol"]))
    all_codes = [c for c in all_codes if len(c) == 6 and c.isdigit()]
    print(f"Universe: {len(all_codes)}", flush=True)

    # Check for existing checkpoint
    done_codes = set()
    all_dfs = []
    if CHECKPOINT.exists():
        cp = pd.read_parquet(CHECKPOINT)
        done_codes = set(cp["code"].unique())
        all_dfs = [cp]
        print(f"Resuming: {len(done_codes)} already fetched", flush=True)

    pending = [c for c in all_codes if c not in done_codes]
    print(f"Pending: {len(pending)}", flush=True)

    started = time.time()
    for idx, code in enumerate(pending):
        try:
            df = fdr.DataReader(code, START, END)
            if df is not None and len(df) > 0:
                df = df.reset_index().rename(columns={"Date": "date"})
                df["code"] = code
                df["name"] = current_map.get(code, dl_map.get(code, ""))
                df["delisted"] = dl_dates.get(code)
                all_dfs.append(df)
        except:
            pass

        done = idx + 1
        if done % 100 == 0 or done == len(pending):
            elapsed = time.time() - started
            rate = done / max(elapsed, 0.001)
            eta = (len(pending) - done) / max(rate, 0.001)
            total_ok = len(all_dfs)
            print(f"[{done}/{len(pending)}] total_ok={total_ok} {elapsed:.1f}s eta={eta:.0f}s", flush=True)

            # Checkpoint save every 500
            if done % 500 == 0:
                cp_df = pd.concat(all_dfs, ignore_index=True)
                cp_df.to_parquet(CHECKPOINT, index=False)
                print(f"  Checkpoint saved: {len(cp_df)} rows", flush=True)

    # Final save
    print("Concatenating and saving...", flush=True)
    panel = pd.concat(all_dfs, ignore_index=True)
    panel = panel.sort_values(["code", "date"]).reset_index(drop=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(OUT, index=False, encoding="utf-8-sig")
    if CHECKPOINT.exists():
        CHECKPOINT.unlink()

    n_delisted = panel[panel["delisted"].notna()]["code"].nunique()
    n_active = panel[panel["delisted"].isna()]["code"].nunique()
    stats = {
        "stocks": int(panel["code"].nunique()),
        "rows": int(len(panel)),
        "delisted": int(n_delisted),
        "active": int(n_active),
        "size_mb": round(OUT.stat().st_size / 1024 / 1024, 1),
        "elapsed": round(time.time() - started, 1),
    }
    print(json.dumps(stats, indent=2), flush=True)

if __name__ == "__main__":
    main()
