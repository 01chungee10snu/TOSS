"""Collect receipt-versioned OpenDART XBRL packages for strict PIT research.

The command is intentionally code-scoped: callers must specify stock codes so a
missing filter cannot trigger thousands of API/archive downloads accidentally.
No broker API is touched.

Example:
    OPENDART_API_KEY=... PYTHONPATH=src .venv/bin/python \
      scripts/collect_opendart_xbrl_archive.py --codes 005930,000660 \
      --begin 2022-01-01 --end 2026-08-27
"""
from __future__ import annotations

import argparse
import csv
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

from toss_alpha.connectors.dart_xbrl_archive import (
    DartXbrlArchiveClient,
    is_periodic_report,
    period_end_from_report_name,
    report_code_from_name,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = ROOT / "reports" / "backtests" / "fundamental" / "opendart_xbrl_raw"
DEFAULT_MANIFEST = ROOT / "reports" / "backtests" / "fundamental" / "opendart_xbrl_manifest.csv"
AMENDMENT_MARKERS = ("정정", "첨부정정", "기재정정")


@dataclass(frozen=True)
class ArchiveRow:
    code: str
    corp_code: str
    corp_name: str
    report_nm: str
    rcept_no: str
    rcept_dt: str
    available_at: str
    period_end: str
    reprt_code: str
    is_amendment: bool
    revision_safe: bool
    source: str
    archive_path: str


def normalize_codes(raw: Iterable[str]) -> list[str]:
    return sorted({str(code).strip().zfill(6) for code in raw if str(code).strip()})


def collect_archive(
    client: DartXbrlArchiveClient,
    *,
    codes: Iterable[str],
    begin_date: str,
    end_date: str,
    raw_dir: Path,
) -> list[ArchiveRow]:
    target_codes = normalize_codes(codes)
    if not target_codes:
        raise ValueError("at least one stock code is required")

    corp_rows = client.corp_code_table()
    by_stock = {str(row.get("stock_code") or "").zfill(6): row for row in corp_rows if row.get("stock_code")}
    result: list[ArchiveRow] = []

    for code in target_codes:
        corp = by_stock.get(code)
        if corp is None:
            continue
        corp_code = str(corp["corp_code"])
        filings = client.list_filings(corp_code=corp_code, begin_date=begin_date, end_date=end_date)
        for filing in filings:
            report_nm = str(filing.get("report_nm") or "")
            if not is_periodic_report(report_nm):
                continue
            reprt_code = report_code_from_name(report_nm)
            period_end = period_end_from_report_name(report_nm)
            rcept_no = str(filing.get("rcept_no") or "").strip()
            rcept_dt = str(filing.get("rcept_dt") or "").strip()
            if not reprt_code or not period_end or not rcept_no or len(rcept_dt) != 8:
                continue

            relative = Path(code) / f"{rcept_no}_{reprt_code}.zip"
            target = raw_dir / relative
            if not target.exists():
                client.save_xbrl(rcept_no=rcept_no, reprt_code=reprt_code, path=target)

            result.append(
                ArchiveRow(
                    code=code,
                    corp_code=corp_code,
                    corp_name=str(corp.get("corp_name") or ""),
                    report_nm=report_nm,
                    rcept_no=rcept_no,
                    rcept_dt=rcept_dt,
                    available_at=f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:8]}",
                    period_end=period_end,
                    reprt_code=reprt_code,
                    is_amendment=any(marker in report_nm for marker in AMENDMENT_MARKERS),
                    revision_safe=True,
                    source="opendart_receipt_xbrl",
                    archive_path=str(relative),
                )
            )
    return sorted(result, key=lambda row: (row.code, row.period_end, row.available_at, row.rcept_no))


def write_manifest(rows: list[ArchiveRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(ArchiveRow.__dataclass_fields__.keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codes", required=True, help="comma-separated KRX stock codes")
    parser.add_argument("--begin", default="2022-01-01")
    parser.add_argument("--end", default="2026-08-27")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_key = os.getenv("OPENDART_API_KEY", "").strip()
    if not api_key:
        print("BLOCKED_MISSING_OPENDART_API_KEY")
        return 2

    client = DartXbrlArchiveClient(api_key=api_key)
    rows = collect_archive(
        client,
        codes=args.codes.split(","),
        begin_date=args.begin,
        end_date=args.end,
        raw_dir=args.raw_dir,
    )
    write_manifest(rows, args.manifest)
    print(f"archived_filings={len(rows)}")
    print(f"manifest={args.manifest}")
    print(f"raw_dir={args.raw_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
