#!/usr/bin/env python3
"""Fetch PIT universe OHLCV panel — batch mode to avoid FDR session deadlock."""
import time, json, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pandas as pd
import FinanceDataReader as fdr

ROOT = Path(__file__).resolve().parents[1]
START, END = "2022-01-01", "2026-07-16"
OUT = ROOT / "reports/backtests/pit_full_universe_2022-01-01_2026_ohlcv_panel.csv"
BATCH = 100
THREADS = 10

def main():
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
    print(f"Universe: {len(all_codes)} ({len(current)} active + {len(dl)} delisted)", flush=True)

    def fetch(code):
        try:
            df = fdr.DataReader(code, START, END)
            if df is None or len(df) == 0:
                return None
            df = df.reset_index().rename(columns={"Date": "date"})
            df["code"] = code
            df["name"] = current_map.get(code, dl_map.get(code, ""))
            df["delisted"] = dl_dates.get(code)
            return df
        except:
            return None

    all_dfs = []
    done = 0
    started = time.time()
    for i in range(0, len(all_codes), BATCH):
        batch = all_codes[i:i + BATCH]
        with ThreadPoolExecutor(max_workers=THREADS) as pool:
            for df in pool.map(fetch, batch):
                if df is not None:
                    all_dfs.append(df)
        done += len(batch)
        if done % 300 == 0 or done >= len(all_codes):
            elapsed = time.time() - started
            rate = done / max(elapsed, 0.001)
            eta = (len(all_codes) - done) / max(rate, 0.001)
            print(f"[{done}/{len(all_codes)}] ok={len(all_dfs)} {elapsed:.1f}s eta={eta:.0f}s", flush=True)

    print(f"Concatenating {len(all_dfs)}...", flush=True)
    panel = pd.concat(all_dfs, ignore_index=True)
    panel = panel.sort_values(["code", "date"]).reset_index(drop=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(OUT, index=False, encoding="utf-8-sig")
    n_delisted = panel[panel["delisted"].notna()]["code"].nunique()
    n_active = panel[panel["delisted"].isna()]["code"].nunique()
    stats = {
        "stocks": int(panel["code"].nunique()),
        "rows": int(len(panel)),
        "delisted": int(n_delisted),
        "active": int(n_active),
        "date_range": f"{panel['date'].min().date()} ~ {panel['date'].max().date()}",
        "size_mb": round(OUT.stat().st_size / 1024 / 1024, 1),
        "elapsed": round(time.time() - started, 1),
    }
    print(json.dumps(stats, indent=2), flush=True)

if __name__ == "__main__":
    main()
