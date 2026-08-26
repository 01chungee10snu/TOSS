"""Collect historical quarterly fundamental data from Naver Finance.

For each stock in the practical universe (400), scrapes the 기업실적분석 table
which contains: 매출액, 영업이익, 당기순이익, ROE, BPS, EPS, PER, PBR, 부채비율.

The table has ~7 quarterly columns + ~4 annual columns, giving us ~2 years
of quarterly history. While not as deep as OpenDART, this is enough to:
  - Construct HML (B/M ratio) at quarterly snapshots
  - Construct CMA (asset growth proxy via 매출액/자산 growth)
  - Backtest quarterly rebalancing with conservative publication-delay timing

Important: Naver exposes a current-view table, not a historical filing-date
snapshot archive. The collector therefore records estimate flags explicitly but
does not by itself make the dataset fully point-in-time (PIT) correct.

Output: reports/backtests/fundamental/naver_quarterly_fundamentals.csv

Usage:
    PYTHONPATH=src .venv/bin/python scripts/collect_naver_quarterly_fundamentals.py
"""
from __future__ import annotations

import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "reports" / "backtests" / "fundamental"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_CSV = OUT_DIR / "naver_quarterly_fundamentals.csv"

PANEL_CSV = ROOT / "reports" / "backtests" / "practical_universe_400_2022-01-01_2026-latest_ohlcv_panel.csv"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

# Row labels we want from the 기업실적분석 table
WANTED_ROWS = {
    "매출액": "revenue",
    "영업이익": "operating_income",
    "당기순이익": "net_income",
    "ROE(지배주주)": "roe",
    "EPS(원)": "eps",
    "BPS(원)": "bps",
    "PER(배)": "per",
    "PBR(배)": "pbr",
    "부채비율": "debt_ratio",
    "유보율": "retention_ratio",
    "순이익률": "net_margin",
    "영업이익률": "op_margin",
}


def parse_number(s: str) -> float | None:
    """Parse Korean-formatted number: '2,589,355' -> 2589355.0"""
    if not s or s.strip() == "":
        return None
    s = s.strip().replace(",", "")
    if s == "" or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def infer_period_types(period_headers: list[str]) -> list[str]:
    """Infer Naver's annual-vs-quarterly header blocks from display order.

    Naver renders annual columns first and quarterly columns second. December
    appears in both blocks (annual FY and quarterly Q4), so month==12 alone is
    not sufficient to classify the row. The first non-December header marks the
    quarterly block in the current table layout; a repeated period is a safe
    fallback when only December headers are present.
    """
    boundary: int | None = None
    for i, period in enumerate(period_headers):
        m = re.match(r"(\d{4})\.(\d{2})", period)
        if m and int(m.group(2)) != 12:
            boundary = i
            break

    if boundary is None:
        seen: set[str] = set()
        for i, period in enumerate(period_headers):
            normalized = re.sub(r"\(E\)$", "", period.strip())
            if normalized in seen:
                boundary = i
                break
            seen.add(normalized)

    if boundary is None:
        boundary = len(period_headers)

    return ["annual" if i < boundary else "quarterly" for i in range(len(period_headers))]


def scrape_quarterly_fundamentals(code: str) -> list[dict]:
    """Scrape 기업실적분석 table from Naver Finance.

    Returns list of dicts, one per period column (quarter or year).
    """
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return []
        html = r.content.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table")

        # Find the 기업실적분석 table (has 주요재무정보 in summary or caption)
        fin_table = None
        for t in tables:
            cap = t.find("caption")
            summary = t.get("summary", "")
            if cap and "기업실적분석" in cap.get_text():
                fin_table = t
                break
            if "주요재무정보" in summary:
                fin_table = t
                break

        if fin_table is None:
            return []

        # Extract period headers (the row with dates like 2023.12, 2024.12)
        period_headers = []
        thead = fin_table.find("thead")
        if thead:
            for th in thead.find_all("th"):
                txt = th.get_text(strip=True)
                if re.match(r"\d{4}\.\d{2}", txt):
                    period_headers.append(txt)

        if not period_headers:
            # Try finding period row directly
            for tr in fin_table.find_all("tr"):
                cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
                for c in cells:
                    if re.match(r"\d{4}\.\d{2}", c):
                        period_headers.append(c)
                if period_headers:
                    break

        if not period_headers:
            return []

        # Extract data rows
        data_rows: dict[str, list[float | None]] = {}
        for tr in fin_table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
            if len(cells) < 2:
                continue
            label = cells[0]
            # Check if this is one of our wanted rows
            matched_key = None
            for wanted_label, field_name in WANTED_ROWS.items():
                if wanted_label in label:
                    matched_key = field_name
                    break

            if matched_key is None:
                continue

            # Values are in cells[1:] — align with period_headers
            values = []
            for c in cells[1:]:
                val = parse_number(c)
                values.append(val)

            # Trim/pad to match period_headers length
            if len(values) >= len(period_headers):
                data_rows[matched_key] = values[:len(period_headers)]
            else:
                data_rows[matched_key] = values + [None] * (len(period_headers) - len(values))

        # Build output records. Preserve display-block semantics because Q4
        # quarterly rows and annual rows can share the same YYYY.12 label.
        period_types = infer_period_types(period_headers)
        records = []
        for i, period in enumerate(period_headers):
            rec: dict = {
                "code": code,
                "period": period,
                "period_type": period_types[i],
                "is_estimate": bool(re.search(r"\(E\)", period, flags=re.IGNORECASE)),
            }
            for field_name, values in data_rows.items():
                rec[field_name] = values[i] if i < len(values) else None

            # Parse period to year/quarter
            m = re.match(r"(\d{4})\.(\d{2})", period)
            if m:
                year = int(m.group(1))
                month = int(m.group(2))
                rec["year"] = year
                rec["month"] = month
                rec["quarter"] = (month - 1) // 3 + 1
            else:
                rec["period_type"] = "unknown"

            records.append(rec)

        return records

    except Exception as e:
        print(f"  Error scraping {code}: {e}", file=sys.stderr)
        return []


def get_universe_codes() -> list[str]:
    """Get list of stock codes from the practical universe panel."""
    panel = pd.read_csv(PANEL_CSV)
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    codes = sorted(panel["code"].unique())
    return codes


def main() -> None:
    print("=== Naver Quarterly Fundamentals Collector ===")
    print(f"Panel: {PANEL_CSV.name}")

    codes = get_universe_codes()
    print(f"Universe: {len(codes)} stocks")

    all_records: list[dict] = []
    success_count = 0
    fail_count = 0

    for i, code in enumerate(codes):
        if (i + 1) % 50 == 0:
            print(f"  Progress: {i+1}/{len(codes)} (success={success_count}, fail={fail_count})")

        records = scrape_quarterly_fundamentals(code)
        if records:
            all_records.extend(records)
            success_count += 1
        else:
            fail_count += 1

        # Be polite to Naver
        time.sleep(0.3)

    print(f"\nDone: {success_count} success, {fail_count} fail")
    print(f"Total records: {len(all_records)}")

    if not all_records:
        print("No data collected!", file=sys.stderr)
        sys.exit(1)

    df = pd.DataFrame(all_records)
    df["collected_at_utc"] = datetime.now(timezone.utc).isoformat()
    df.to_csv(OUT_CSV, index=False)
    print(f"Saved: {OUT_CSV}")
    print(f"Shape: {df.shape}")
    print(f"\nPeriod coverage: {sorted(df['period'].unique())}")
    print(f"Quarterly records: {(df['period_type']=='quarterly').sum()}")
    print(f"Annual records: {(df['period_type']=='annual').sum()}")
    print(f"Estimate records: {df['is_estimate'].sum()}")


if __name__ == "__main__":
    main()
