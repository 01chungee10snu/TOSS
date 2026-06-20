from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
BACKTEST_DIR = ROOT / "reports/backtests"
BASE_PANEL = BACKTEST_DIR / "random500_seed20260607_2022-01-01_2025-12-31_ohlcv_panel.csv"
SAMPLE_CSV = BACKTEST_DIR / "random500_seed20260607_ma20_60_2022-01-01_2025-12-31_sample.csv"
OUT_PANEL = BACKTEST_DIR / "random500_seed20260607_2022-01-01_2026-latest_ohlcv_panel.csv"
STATUS_JSON = BACKTEST_DIR / "random500_seed20260607_2026_update_status.json"
START_2026 = "2026-01-01"
# yfinance end is exclusive. Use tomorrow relative to runtime.
YF_END = (date.today() + timedelta(days=1)).isoformat()


def _download_one(ticker: str) -> pd.DataFrame:
    df = yf.download(ticker, start=START_2026, end=YF_END, auto_adjust=True, progress=False, threads=False)
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.reset_index()
    df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col not in df.columns:
            df[col] = pd.NA
    return df[["Date", "Open", "High", "Low", "Close", "Volume"]].sort_values("Date").reset_index(drop=True)


def main() -> None:
    base = pd.read_csv(BASE_PANEL, dtype={"code": str}, parse_dates=["Date"])
    base["code"] = base["code"].astype(str).str.zfill(6)
    sample = pd.read_csv(SAMPLE_CSV, dtype={"code": str})
    sample["code"] = sample["code"].astype(str).str.zfill(6)
    active_codes = set(base["code"].unique().tolist())
    sample = sample[sample["code"].isin(active_codes)].copy()

    frames: list[pd.DataFrame] = []
    status = {
        "base_panel": str(BASE_PANEL),
        "out_panel": str(OUT_PANEL),
        "start_2026": START_2026,
        "yf_end_exclusive": YF_END,
        "base_rows": int(len(base)),
        "base_codes": int(base["code"].nunique()),
        "download": {},
    }
    for i, row in sample.iterrows():
        code = str(row["code"]).zfill(6)
        ticker = row["yfinance_symbol"]
        name = row.get("name", "")
        print(f"[{len(status['download'])+1:03d}/{len(sample)}] {code} {name} {ticker}", flush=True)
        try:
            df = _download_one(ticker)
            if df.empty:
                status["download"][code] = {"status": "NO_DATA", "ticker": ticker, "name": name, "rows": 0}
                continue
            df["code"] = code
            df["name"] = name
            frames.append(df)
            status["download"][code] = {
                "status": "PASS",
                "ticker": ticker,
                "name": name,
                "rows": int(len(df)),
                "first": str(df["Date"].min().date()),
                "last": str(df["Date"].max().date()),
            }
        except Exception as exc:
            status["download"][code] = {"status": "ERROR", "ticker": ticker, "name": name, "error": repr(exc)}
        time.sleep(0.05)

    if frames:
        add = pd.concat(frames, ignore_index=True)
        combined = pd.concat([base, add], ignore_index=True)
        combined["Date"] = pd.to_datetime(combined["Date"])
        combined["code"] = combined["code"].astype(str).str.zfill(6)
        combined = combined.drop_duplicates(subset=["Date", "code"], keep="last")
        combined = combined.sort_values(["Date", "code"]).reset_index(drop=True)
    else:
        combined = base

    combined.to_csv(OUT_PANEL, index=False)
    status["added_rows"] = int(sum(len(f) for f in frames))
    status["combined_rows"] = int(len(combined))
    status["combined_codes"] = int(combined["code"].nunique())
    status["combined_start"] = str(combined["Date"].min().date())
    status["combined_end"] = str(combined["Date"].max().date())
    counts = {}
    for item in status["download"].values():
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    status["counts"] = counts
    STATUS_JSON.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: status[k] for k in ["out_panel", "added_rows", "combined_rows", "combined_codes", "combined_start", "combined_end", "counts"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
