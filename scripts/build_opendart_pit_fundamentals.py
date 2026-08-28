"""Build revision-safe PIT factor inputs from archived OpenDART XBRL packages.

Input is the receipt-versioned manifest created by
``collect_opendart_xbrl_archive.py``.  Every original filing/amendment remains a
separate row with its own ``available_at``.  The builder does not collapse to a
latest value and therefore does not introduce future amendment information.

No broker API is used.  This script only reads already archived XBRL ZIP files.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from toss_alpha.research.factor_pit import validate_pit_contract
from toss_alpha.research.xbrl_facts import parse_xbrl_archive

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "reports" / "backtests" / "fundamental" / "opendart_xbrl_manifest.csv"
DEFAULT_RAW_DIR = ROOT / "reports" / "backtests" / "fundamental" / "opendart_xbrl_raw"
DEFAULT_OUT = ROOT / "reports" / "backtests" / "fundamental" / "opendart_pit_fundamentals.csv"
DEFAULT_STOCK_TOTALS = ROOT / "reports" / "backtests" / "fundamental" / "opendart_stock_totals_receipt_matched.csv"
DEFAULT_AUDIT = ROOT / "reports" / "validation" / "opendart_pit_fundamentals_build_latest.json"
DEFAULT_PANEL = ROOT / "reports" / "backtests" / "pit_full_universe_2022-01-01_2026_ohlcv_panel.csv"


def _bool_value(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _numeric(value: Any) -> float | None:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return None
    return float(number)


def _same_share_count(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) <= max(1.0, abs(float(a)) * 1e-10)


def _remove_share_reason(value: Any) -> str:
    parts = [item for item in str(value or "").split(";") if item and not item.startswith("shares_outstanding:")]
    return ";".join(parts)


def apply_receipt_matched_stock_totals(frame: pd.DataFrame, stock_totals: pd.DataFrame | None) -> pd.DataFrame:
    """Fill stock counts only from exact receipt-matched, revision-safe OpenDART rows."""
    if frame.empty or stock_totals is None or stock_totals.empty:
        return frame
    required = {
        "code", "period_end", "rcept_no", "reprt_code", "source", "revision_safe",
        "issued_common_shares", "distributed_common_shares",
    }
    missing = sorted(required.difference(stock_totals.columns))
    if missing:
        raise ValueError(f"stock totals missing columns: {','.join(missing)}")

    supplement = stock_totals.copy()
    supplement["code"] = supplement["code"].astype(str).str.zfill(6)
    supplement["rcept_no"] = supplement["rcept_no"].astype(str).str.strip()
    supplement["reprt_code"] = supplement["reprt_code"].astype(str).str.strip()
    supplement["period_end"] = supplement["period_end"].astype(str).str[:10]
    supplement = supplement[
        supplement["source"].astype(str).eq("opendart_stock_total_receipt_matched")
        & supplement["revision_safe"].map(_bool_value)
    ].copy()
    keys = ["code", "period_end", "rcept_no", "reprt_code"]
    supplement = supplement.sort_values(keys).drop_duplicates(keys, keep=False)
    lookup = {tuple(str(row[key]) for key in keys): row for _, row in supplement.iterrows()}

    result = frame.copy()
    if "stock_total_receipt_matched" not in result.columns:
        result["stock_total_receipt_matched"] = False
    if "shares_outstanding_source" not in result.columns:
        result["shares_outstanding_source"] = result.get("shares_status", pd.Series(index=result.index, dtype=object)).map(
            lambda value: "opendart_receipt_xbrl" if str(value) in {"SELECTED", "DERIVED"} else None
        )
    if "shares_issued_source" not in result.columns:
        result["shares_issued_source"] = result.get("shares_issued", pd.Series(index=result.index, dtype=float)).map(
            lambda value: "opendart_receipt_xbrl" if _numeric(value) is not None else None
        )
    result["stock_total_conflict"] = False

    for idx, row in result.iterrows():
        key = (
            str(row.get("code") or "").zfill(6),
            str(row.get("period_end") or "")[:10],
            str(row.get("rcept_no") or "").strip(),
            str(row.get("reprt_code") or "").strip(),
        )
        stock = lookup.get(key)
        if stock is None:
            continue
        result.at[idx, "stock_total_receipt_matched"] = True
        distributed = _numeric(stock.get("distributed_common_shares"))
        issued = _numeric(stock.get("issued_common_shares"))
        existing_outstanding = _numeric(row.get("shares_outstanding"))
        existing_issued = _numeric(row.get("shares_issued"))

        if existing_outstanding is not None and distributed is not None and not _same_share_count(existing_outstanding, distributed):
            result.at[idx, "shares_outstanding"] = None
            result.at[idx, "bps"] = None
            result.at[idx, "shares_status"] = "CONFLICT"
            result.at[idx, "shares_outstanding_source"] = None
            result.at[idx, "stock_total_conflict"] = True
            base_reason = _remove_share_reason(row.get("parse_reason"))
            conflict_reason = "shares_outstanding:CONFLICT:xbrl_vs_receipt_matched_stock_total"
            result.at[idx, "parse_reason"] = ";".join(item for item in [base_reason, conflict_reason] if item)
        elif existing_outstanding is None and distributed is not None and distributed > 0:
            result.at[idx, "shares_outstanding"] = distributed
            result.at[idx, "shares_status"] = "SUPPLEMENTED"
            result.at[idx, "shares_derivation_reason"] = "exact_receipt_matched_stock_total_status"
            result.at[idx, "shares_concept"] = "OpenDART:distb_stock_co"
            result.at[idx, "shares_context_id"] = f"rcept_no:{key[2]}"
            result.at[idx, "shares_outstanding_source"] = "opendart_stock_total_receipt_matched"
            result.at[idx, "parse_reason"] = _remove_share_reason(row.get("parse_reason"))

        if existing_issued is not None and issued is not None and not _same_share_count(existing_issued, issued):
            result.at[idx, "stock_total_conflict"] = True
        elif existing_issued is None and issued is not None and issued > 0:
            result.at[idx, "shares_issued"] = issued
            result.at[idx, "shares_issued_concept"] = "OpenDART:istc_totqy"
            result.at[idx, "shares_issued_context_id"] = f"rcept_no:{key[2]}"
            result.at[idx, "shares_issued_source"] = "opendart_stock_total_receipt_matched"

        equity = _numeric(result.at[idx, "book_equity"] if "book_equity" in result.columns else None)
        shares = _numeric(result.at[idx, "shares_outstanding"] if "shares_outstanding" in result.columns else None)
        assets = _numeric(result.at[idx, "assets"] if "assets" in result.columns else None)
        if not bool(result.at[idx, "stock_total_conflict"]) and equity is not None and shares is not None and equity > 0 and shares > 0:
            result.at[idx, "bps"] = equity / shares
        if assets is not None and equity is not None and _numeric(result.at[idx, "bps"]) is not None and not bool(result.at[idx, "stock_total_conflict"]):
            result.at[idx, "parse_status"] = "READY"
        elif str(result.at[idx, "parse_status"]) == "READY" and bool(result.at[idx, "stock_total_conflict"]):
            result.at[idx, "parse_status"] = "INCOMPLETE"
    return result


def build_rows(manifest: pd.DataFrame, *, raw_dir: Path, stock_totals: pd.DataFrame | None = None) -> pd.DataFrame:
    required = {
        "code",
        "period_end",
        "available_at",
        "rcept_no",
        "reprt_code",
        "source",
        "revision_safe",
        "archive_path",
    }
    missing = sorted(required.difference(manifest.columns))
    if missing:
        raise ValueError(f"manifest missing columns: {','.join(missing)}")

    rows: list[dict[str, Any]] = []
    for _, item in manifest.iterrows():
        code = str(item.get("code") or "").strip().zfill(6)
        archive_rel = str(item.get("archive_path") or "").strip()
        archive = raw_dir / archive_rel
        base = {
            "code": code,
            "corp_code": str(item.get("corp_code") or "").strip(),
            "corp_name": str(item.get("corp_name") or "").strip(),
            "period_end": str(item.get("period_end") or "").strip(),
            "available_at": str(item.get("available_at") or "").strip(),
            "rcept_no": str(item.get("rcept_no") or "").strip(),
            "reprt_code": str(item.get("reprt_code") or "").strip(),
            "source": str(item.get("source") or "opendart_receipt_xbrl").strip(),
            "revision_safe": _bool_value(item.get("revision_safe", True)),
            "is_estimate": False,
            "is_amendment": _bool_value(item.get("is_amendment", False)),
            "archive_path": archive_rel,
        }
        if not archive.exists():
            rows.append(
                base
                | {
                    "parse_status": "MISSING_ARCHIVE",
                    "parse_reason": "archive_file_missing",
                    "assets": None,
                    "book_equity": None,
                    "revenue": None,
                    "operating_income": None,
                    "net_income": None,
                    "shares_outstanding": None,
                    "shares_issued": None,
                    "treasury_shares": None,
                    "shares_status": "MISSING",
                    "shares_derivation_reason": None,
                    "bps": None,
                    "revenue_basis": None,
                    "profitability_basis": None,
                    "operating_profitability_proxy": None,
                    "roe_proxy": None,
                    "profitability_status": "INCOMPLETE",
                }
            )
            continue

        try:
            parsed = parse_xbrl_archive(
                archive,
                period_end=base["period_end"],
                reprt_code=base["reprt_code"],
            )
        except Exception as exc:  # one malformed filing must not erase the rest of the PIT dataset
            rows.append(
                base
                | {
                    "parse_status": "PARSE_ERROR",
                    "parse_reason": f"{type(exc).__name__}:{str(exc)[:200]}",
                    "assets": None,
                    "book_equity": None,
                    "revenue": None,
                    "operating_income": None,
                    "net_income": None,
                    "shares_outstanding": None,
                    "shares_issued": None,
                    "treasury_shares": None,
                    "shares_status": "MISSING",
                    "shares_derivation_reason": None,
                    "bps": None,
                    "revenue_basis": None,
                    "profitability_basis": None,
                    "operating_profitability_proxy": None,
                    "roe_proxy": None,
                    "profitability_status": "INCOMPLETE",
                }
            )
            continue

        selections = {
            "assets": parsed.assets,
            "book_equity": parsed.book_equity,
            "revenue": parsed.revenue,
            "operating_income": parsed.operating_income,
            "net_income": parsed.net_income,
            "shares_outstanding": parsed.shares_outstanding,
        }
        reasons = []
        for name, selection in selections.items():
            allowed = {"SELECTED", "DERIVED"} if name == "shares_outstanding" else {"SELECTED"}
            if selection.status not in allowed:
                reasons.append(f"{name}:{selection.status}:{selection.reason or ''}".rstrip(":"))
        rows.append(
            base
            | {
                "parse_status": "READY" if parsed.ready_for_hml_cma else "INCOMPLETE",
                "parse_reason": ";".join(reasons),
                "assets": parsed.assets.value,
                "book_equity": parsed.book_equity.value,
                "revenue": parsed.revenue.value,
                "operating_income": parsed.operating_income.value,
                "net_income": parsed.net_income.value,
                "shares_outstanding": parsed.shares_outstanding.value,
                "shares_issued": parsed.shares_issued.value,
                "treasury_shares": parsed.treasury_shares.value,
                "shares_status": parsed.shares_outstanding.status,
                "shares_derivation_reason": parsed.shares_outstanding.reason,
                "bps": parsed.bps,
                "revenue_basis": parsed.revenue_basis,
                "profitability_basis": parsed.profitability_basis,
                "operating_profitability_proxy": parsed.operating_profitability_proxy,
                "roe_proxy": parsed.roe_proxy,
                "profitability_status": "READY" if parsed.ready_for_profitability else "INCOMPLETE",
                "assets_concept": parsed.assets.concept,
                "equity_concept": parsed.book_equity.concept,
                "revenue_concept": parsed.revenue.concept,
                "operating_income_concept": parsed.operating_income.concept,
                "net_income_concept": parsed.net_income.concept,
                "shares_concept": parsed.shares_outstanding.concept,
                "shares_issued_concept": parsed.shares_issued.concept,
                "treasury_shares_concept": parsed.treasury_shares.concept,
                "assets_context_id": parsed.assets.context_id,
                "equity_context_id": parsed.book_equity.context_id,
                "revenue_context_id": parsed.revenue.context_id,
                "operating_income_context_id": parsed.operating_income.context_id,
                "net_income_context_id": parsed.net_income.context_id,
                "shares_context_id": parsed.shares_outstanding.context_id,
                "shares_issued_context_id": parsed.shares_issued.context_id,
                "treasury_shares_context_id": parsed.treasury_shares.context_id,
                "revenue_duration_days": parsed.revenue.duration_days,
                "operating_income_duration_days": parsed.operating_income.duration_days,
                "net_income_duration_days": parsed.net_income.duration_days,
                "instance_count": parsed.instance_count,
                "raw_fact_count": parsed.raw_fact_count,
            }
        )

    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["code"] = frame["code"].astype(str).str.zfill(6)
        frame = frame.sort_values(["code", "period_end", "available_at", "rcept_no"]).reset_index(drop=True)
        frame = apply_receipt_matched_stock_totals(frame, stock_totals)
    return frame


def build_audit(frame: pd.DataFrame, *, pit_panel: pd.DataFrame | None = None) -> dict[str, Any]:
    status_counts = frame.get("parse_status", pd.Series(dtype=str)).value_counts().to_dict()
    report: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "opendart_receipt_versioned_pit_fundamentals_build",
        "rows": int(len(frame)),
        "codes": int(frame["code"].nunique()) if "code" in frame.columns else 0,
        "status_counts": {str(k): int(v) for k, v in status_counts.items()},
        "ready_rows": int((frame.get("parse_status") == "READY").sum()) if not frame.empty else 0,
        "profitability_ready_rows": int((frame.get("profitability_status") == "READY").sum()) if not frame.empty else 0,
        "amendment_rows": int(frame.get("is_amendment", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()),
        "revision_semantics": "each receipt/amendment preserved as separate available_at version",
        "latest_value_collapsing_performed": False,
    }
    if pit_panel is not None and not frame.empty:
        report["pit_contract"] = validate_pit_contract(
            frame,
            pit_panel,
            required_value_columns=("bps", "assets"),
        ).to_dict()
        report["profitability_pit_contract"] = validate_pit_contract(
            frame,
            pit_panel,
            required_value_columns=("book_equity", "operating_income"),
        ).to_dict()
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--stock-totals", type=Path, default=DEFAULT_STOCK_TOTALS)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--pit-panel", type=Path, default=DEFAULT_PANEL)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.manifest.exists():
        print(f"BLOCKED_MISSING_MANIFEST:{args.manifest}")
        return 2
    manifest = pd.read_csv(args.manifest, dtype={"code": str, "rcept_no": str, "reprt_code": str})
    stock_totals = (
        pd.read_csv(args.stock_totals, dtype={"code": str, "rcept_no": str, "reprt_code": str})
        if args.stock_totals.exists()
        else None
    )
    frame = build_rows(manifest, raw_dir=args.raw_dir, stock_totals=stock_totals)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.out, index=False, encoding="utf-8-sig")

    panel = pd.read_csv(args.pit_panel, dtype={"code": str}) if args.pit_panel.exists() else None
    audit = build_audit(frame, pit_panel=panel)
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(f"rows={len(frame)}")
    print(f"ready_rows={audit['ready_rows']}")
    if "pit_contract" in audit:
        print(f"pit_contract={audit['pit_contract']['status']}")
    print(f"out={args.out}")
    print(f"audit={args.audit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
