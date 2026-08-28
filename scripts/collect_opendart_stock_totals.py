"""Collect receipt-matched OpenDART stock-total facts for PIT factor research.

The periodic-report stock-total endpoint is queried by company/year/report code,
but its response includes the source disclosure receipt number.  A row is
accepted only when that receipt number exactly matches a receipt already stored
in the XBRL archive manifest.  This prevents a later correction from being
silently backfilled into an earlier point-in-time snapshot.

No broker API is used.  The OpenDART API key is read from OPENDART_API_KEY and
is never written to output files.
"""
from __future__ import annotations

import argparse
import csv
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import pandas as pd

from toss_alpha.connectors.dart_xbrl_archive import DartXbrlArchiveClient

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "reports" / "backtests" / "fundamental" / "opendart_xbrl_manifest.csv"
DEFAULT_OUT = ROOT / "reports" / "backtests" / "fundamental" / "opendart_stock_totals_receipt_matched.csv"
DEFAULT_ISSUES = ROOT / "reports" / "backtests" / "fundamental" / "opendart_stock_totals_issues.csv"


@dataclass(frozen=True)
class StockTotalRow:
    code: str
    corp_code: str
    corp_name: str
    period_end: str
    available_at: str
    rcept_no: str
    reprt_code: str
    security_type: str
    issued_common_shares: float | None
    treasury_common_shares: float | None
    distributed_common_shares: float | None
    settlement_date: str
    source: str = "opendart_stock_total_receipt_matched"
    revision_safe: bool = True


@dataclass(frozen=True)
class StockTotalIssue:
    code: str
    corp_code: str
    business_year: str
    reprt_code: str
    rcept_no: str
    issue: str


def _number(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text in {"-", "—", "–"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if number >= 0 else None


def _ordinary_security(value: Any) -> bool:
    text = str(value or "").strip().replace(" ", "")
    return "보통" in text and "우선" not in text and "합계" not in text and "비고" not in text


def _same_number(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) <= max(1.0, abs(float(a)) * 1e-10)


def collect_stock_totals(
    client: DartXbrlArchiveClient,
    manifest: pd.DataFrame,
) -> tuple[list[StockTotalRow], list[StockTotalIssue]]:
    required = {"code", "corp_code", "corp_name", "period_end", "available_at", "rcept_no", "reprt_code"}
    missing = sorted(required.difference(manifest.columns))
    if missing:
        raise ValueError(f"manifest missing columns: {','.join(missing)}")

    frame = manifest.copy()
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame["rcept_no"] = frame["rcept_no"].astype(str).str.strip()
    frame["reprt_code"] = frame["reprt_code"].astype(str).str.strip()
    frame["_period"] = pd.to_datetime(frame["period_end"], errors="coerce")
    frame = frame[frame["_period"].notna()].copy()
    frame["business_year"] = frame["_period"].dt.year.astype(int).astype(str)

    rows: list[StockTotalRow] = []
    issues: list[StockTotalIssue] = []
    query_columns = ["corp_code", "business_year", "reprt_code"]
    for (corp_code, business_year, reprt_code), group in frame.groupby(query_columns, sort=True):
        response_rows = client.stock_total_status(
            corp_code=str(corp_code),
            business_year=str(business_year),
            reprt_code=str(reprt_code),
        )
        if not response_rows:
            for receipt in group["rcept_no"].astype(str).unique():
                issues.append(
                    StockTotalIssue(
                        code=str(group.iloc[0]["code"]),
                        corp_code=str(corp_code),
                        business_year=str(business_year),
                        reprt_code=str(reprt_code),
                        rcept_no=str(receipt),
                        issue="stock_total_api_no_data",
                    )
                )
            continue

        by_receipt: dict[str, list[dict[str, Any]]] = {}
        for item in response_rows:
            receipt = str(item.get("rcept_no") or "").strip()
            if receipt:
                by_receipt.setdefault(receipt, []).append(item)

        for _, manifest_row in group.iterrows():
            receipt = str(manifest_row["rcept_no"]).strip()
            exact = [item for item in by_receipt.get(receipt, []) if _ordinary_security(item.get("se"))]
            if not exact:
                issues.append(
                    StockTotalIssue(
                        code=str(manifest_row["code"]),
                        corp_code=str(corp_code),
                        business_year=str(business_year),
                        reprt_code=str(reprt_code),
                        rcept_no=receipt,
                        issue="receipt_not_returned_or_ordinary_row_missing",
                    )
                )
                continue

            normalized = [
                (
                    _number(item.get("istc_totqy")),
                    _number(item.get("tesstk_co")),
                    _number(item.get("distb_stock_co")),
                    str(item.get("stlm_dt") or "").strip(),
                    str(item.get("se") or "").strip(),
                )
                for item in exact
            ]
            first = normalized[0]
            if any(
                not (_same_number(first[0], item[0]) and _same_number(first[1], item[1]) and _same_number(first[2], item[2]))
                for item in normalized[1:]
            ):
                issues.append(
                    StockTotalIssue(
                        code=str(manifest_row["code"]),
                        corp_code=str(corp_code),
                        business_year=str(business_year),
                        reprt_code=str(reprt_code),
                        rcept_no=receipt,
                        issue="conflicting_ordinary_stock_total_rows",
                    )
                )
                continue

            issued, treasury, distributed, settlement_date, security_type = first
            if distributed is None and issued is not None and treasury is not None and issued >= treasury:
                distributed = issued - treasury
            if issued is None and distributed is None:
                issues.append(
                    StockTotalIssue(
                        code=str(manifest_row["code"]),
                        corp_code=str(corp_code),
                        business_year=str(business_year),
                        reprt_code=str(reprt_code),
                        rcept_no=receipt,
                        issue="ordinary_stock_total_values_missing",
                    )
                )
                continue

            rows.append(
                StockTotalRow(
                    code=str(manifest_row["code"]).zfill(6),
                    corp_code=str(corp_code),
                    corp_name=str(manifest_row.get("corp_name") or ""),
                    period_end=str(manifest_row.get("period_end") or ""),
                    available_at=str(manifest_row.get("available_at") or ""),
                    rcept_no=receipt,
                    reprt_code=str(reprt_code),
                    security_type=security_type,
                    issued_common_shares=issued,
                    treasury_common_shares=treasury,
                    distributed_common_shares=distributed,
                    settlement_date=settlement_date,
                )
            )

    rows.sort(key=lambda row: (row.code, row.period_end, row.available_at, row.rcept_no))
    issues.sort(key=lambda row: (row.code, row.business_year, row.reprt_code, row.rcept_no, row.issue))
    return rows, issues


def _write_dataclasses(items: list[Any], path: Path, fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in items:
            writer.writerow(asdict(item))


def _cached_receipts(*paths: Path) -> set[str]:
    receipts: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        try:
            frame = pd.read_csv(path, dtype={"rcept_no": str})
        except Exception:
            continue
        if "rcept_no" in frame.columns:
            receipts.update(frame["rcept_no"].dropna().astype(str).str.strip())
    return {value for value in receipts if value}


def _write_incremental_results(
    *,
    current_manifest: pd.DataFrame,
    existing_path: Path,
    new_items: list[Any],
    fieldnames: list[str],
) -> None:
    current_receipts = set(current_manifest["rcept_no"].dropna().astype(str).str.strip())
    existing = pd.DataFrame(columns=fieldnames)
    if existing_path.exists():
        existing = pd.read_csv(existing_path, dtype={"rcept_no": str})
        if "rcept_no" in existing.columns:
            existing["rcept_no"] = existing["rcept_no"].astype(str).str.strip()
            existing = existing[existing["rcept_no"].isin(current_receipts)].copy()
    fresh = pd.DataFrame([asdict(item) for item in new_items], columns=fieldnames)
    if existing.empty:
        combined = fresh.copy()
    elif fresh.empty:
        combined = existing.copy()
    else:
        combined = pd.concat([existing, fresh], ignore_index=True)
    if not combined.empty and "rcept_no" in combined.columns:
        combined = combined.drop_duplicates(subset=["rcept_no"], keep="last")
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(existing_path, index=False, encoding="utf-8-sig")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--issues", type=Path, default=DEFAULT_ISSUES)
    parser.add_argument("--refresh", action="store_true", help="ignore cached receipt results and query every manifest receipt")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_key = str(os.getenv("OPENDART_API_KEY") or "").strip()
    if not api_key:
        print("BLOCKED_MISSING_OPENDART_API_KEY")
        return 2
    if not args.manifest.exists():
        print(f"BLOCKED_MISSING_MANIFEST:{args.manifest}")
        return 2

    manifest = pd.read_csv(args.manifest, dtype={"code": str, "corp_code": str, "rcept_no": str, "reprt_code": str})
    manifest["rcept_no"] = manifest["rcept_no"].astype(str).str.strip()
    cached = set() if args.refresh else _cached_receipts(args.out, args.issues)
    pending = manifest[~manifest["rcept_no"].isin(cached)].copy()
    rows, issues = collect_stock_totals(DartXbrlArchiveClient(api_key=api_key), pending) if not pending.empty else ([], [])
    _write_incremental_results(
        current_manifest=manifest,
        existing_path=args.out,
        new_items=rows,
        fieldnames=list(StockTotalRow.__dataclass_fields__),
    )
    _write_incremental_results(
        current_manifest=manifest,
        existing_path=args.issues,
        new_items=issues,
        fieldnames=list(StockTotalIssue.__dataclass_fields__),
    )
    total_rows = len(pd.read_csv(args.out)) if args.out.exists() else 0
    total_issues = len(pd.read_csv(args.issues)) if args.issues.exists() else 0
    print(f"cached_receipts={len(cached)}")
    print(f"queried_receipts={len(pending)}")
    print(f"receipt_matched_stock_total_rows={total_rows}")
    print(f"stock_total_issues={total_issues}")
    print(f"out={args.out}")
    print(f"issues={args.issues}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
