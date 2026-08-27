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
DEFAULT_AUDIT = ROOT / "reports" / "validation" / "opendart_pit_fundamentals_build_latest.json"
DEFAULT_PANEL = ROOT / "reports" / "backtests" / "pit_full_universe_2022-01-01_2026_ohlcv_panel.csv"


def _bool_value(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def build_rows(manifest: pd.DataFrame, *, raw_dir: Path) -> pd.DataFrame:
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
                    "shares_outstanding": None,
                    "bps": None,
                    "revenue_basis": None,
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
                    "shares_outstanding": None,
                    "bps": None,
                    "revenue_basis": None,
                }
            )
            continue

        selections = {
            "assets": parsed.assets,
            "book_equity": parsed.book_equity,
            "revenue": parsed.revenue,
            "shares_outstanding": parsed.shares_outstanding,
        }
        reasons = [
            f"{name}:{selection.status}:{selection.reason or ''}".rstrip(":")
            for name, selection in selections.items()
            if selection.status != "SELECTED"
        ]
        rows.append(
            base
            | {
                "parse_status": "READY" if parsed.ready_for_hml_cma else "INCOMPLETE",
                "parse_reason": ";".join(reasons),
                "assets": parsed.assets.value,
                "book_equity": parsed.book_equity.value,
                "revenue": parsed.revenue.value,
                "shares_outstanding": parsed.shares_outstanding.value,
                "bps": parsed.bps,
                "revenue_basis": parsed.revenue_basis,
                "assets_concept": parsed.assets.concept,
                "equity_concept": parsed.book_equity.concept,
                "revenue_concept": parsed.revenue.concept,
                "shares_concept": parsed.shares_outstanding.concept,
                "assets_context_id": parsed.assets.context_id,
                "equity_context_id": parsed.book_equity.context_id,
                "revenue_context_id": parsed.revenue.context_id,
                "shares_context_id": parsed.shares_outstanding.context_id,
                "revenue_duration_days": parsed.revenue.duration_days,
                "instance_count": parsed.instance_count,
                "raw_fact_count": parsed.raw_fact_count,
            }
        )

    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["code"] = frame["code"].astype(str).str.zfill(6)
        frame = frame.sort_values(["code", "period_end", "available_at", "rcept_no"]).reset_index(drop=True)
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
        "amendment_rows": int(frame.get("is_amendment", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()),
        "revision_semantics": "each receipt/amendment preserved as separate available_at version",
        "latest_value_collapsing_performed": False,
    }
    if pit_panel is not None and not frame.empty:
        report["pit_contract"] = validate_pit_contract(frame, pit_panel).to_dict()
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--pit-panel", type=Path, default=DEFAULT_PANEL)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.manifest.exists():
        print(f"BLOCKED_MISSING_MANIFEST:{args.manifest}")
        return 2
    manifest = pd.read_csv(args.manifest, dtype={"code": str, "rcept_no": str, "reprt_code": str})
    frame = build_rows(manifest, raw_dir=args.raw_dir)
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
