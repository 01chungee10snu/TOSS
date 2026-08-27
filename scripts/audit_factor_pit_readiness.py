"""Audit whether factor research inputs qualify for a true point-in-time backtest.

Preference order is strict:
1. receipt-versioned OpenDART fundamentals built from archived XBRL packages;
2. existing Naver current-view fundamentals only as a fail-closed diagnostic.

Passing this input contract never promotes a strategy to live trading.  It only
allows a new independent OOS factor backtest to begin.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from toss_alpha.research.factor_pit import naver_current_view_candidate, validate_pit_contract

ROOT = Path(__file__).resolve().parents[1]
NAVER_CSV = ROOT / "reports" / "backtests" / "fundamental" / "naver_quarterly_fundamentals.csv"
OPENDART_PIT_CSV = ROOT / "reports" / "backtests" / "fundamental" / "opendart_pit_fundamentals.csv"
PIT_PANEL_CSV = ROOT / "reports" / "backtests" / "pit_full_universe_2022-01-01_2026_ohlcv_panel.csv"
OUT = ROOT / "reports" / "validation" / "factor_pit_readiness_latest.json"


def build_readiness_report(
    candidate: pd.DataFrame,
    pit_panel: pd.DataFrame,
    *,
    source: str,
    opendart_key_present: bool,
) -> dict[str, Any]:
    contract = validate_pit_contract(candidate, pit_panel)
    true_pit_source = source == "opendart_receipt_xbrl"
    input_ready = bool(contract.eligible and true_pit_source)
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "factor_true_pit_readiness_gate",
        "current_input": {
            "source": source,
            "rows": int(len(candidate)),
            "codes": int(candidate["code"].astype(str).str.zfill(6).nunique()) if "code" in candidate.columns else 0,
            "receipt_versioned": true_pit_source,
        },
        "opendart_collection": {
            "api_key_present": bool(opendart_key_present),
            "required_snapshot_semantics": "original_filing_or_revision_version_available_at_that_time",
            "single_account_latest_view_is_not_revision_safe": True,
        },
        "contract": contract.to_dict(),
        "promotion": {
            "status": "TRUE_PIT_INPUTS_READY" if input_ready else "BLOCKED_TRUE_PIT_INPUTS",
            "hml_cma_live_promotion_allowed": False,
            "factor_backtest_allowed": input_ready,
            "reason": (
                "inputs_ready_but_strategy_requires_new_independent_oos_backtest"
                if input_ready
                else "true_pit_contract_or_source_not_satisfied"
            ),
        },
        "next_requirements": (
            [
                "rerun HML/CMA using as-of revision-aware snapshots and the historical PIT universe",
                "use t-to-next-session execution and 31/50/75bp cost stress",
                "keep strategy research-only until independent OOS evidence passes",
            ]
            if input_ready
            else [
                "collect/build receipt-versioned OpenDART XBRL fundamentals with actual available_at dates",
                "resolve missing or ambiguous BPS/revenue facts without unsafe fallbacks",
                "retain filing receipt identifiers and amendment versions",
                "intersect every future rebalance snapshot with the historical PIT universe",
            ]
        ),
    }


def load_preferred_candidate(
    *,
    opendart_path: Path = OPENDART_PIT_CSV,
    naver_path: Path = NAVER_CSV,
) -> tuple[pd.DataFrame, str]:
    """Load receipt-versioned OpenDART data when present, otherwise Naver diagnostic data."""
    if opendart_path.exists():
        frame = pd.read_csv(
            opendart_path,
            dtype={"code": str, "rcept_no": str, "reprt_code": str},
        )
        return frame, "opendart_receipt_xbrl"
    if not naver_path.exists():
        raise FileNotFoundError(f"neither OpenDART PIT nor Naver fundamentals exist: {opendart_path}, {naver_path}")
    naver = pd.read_csv(naver_path, dtype={"code": str})
    return naver_current_view_candidate(naver), "naver_current_view"


def main() -> int:
    if not PIT_PANEL_CSV.exists():
        raise FileNotFoundError(PIT_PANEL_CSV)

    candidate, source = load_preferred_candidate()
    pit_panel = pd.read_csv(PIT_PANEL_CSV, dtype={"code": str})
    report = build_readiness_report(
        candidate,
        pit_panel,
        source=source,
        opendart_key_present=bool(os.getenv("OPENDART_API_KEY")),
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    contract = report["contract"]
    print(f"factor_pit_source={source}")
    print(f"factor_pit_status={contract['status']}")
    print(f"eligible={contract['eligible']}")
    print(f"factor_backtest_allowed={report['promotion']['factor_backtest_allowed']}")
    print(f"reasons={','.join(contract['reasons'])}")
    print(f"opendart_api_key_present={report['opendart_collection']['api_key_present']}")
    print(f"output={OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
