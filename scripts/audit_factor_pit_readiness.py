"""Audit whether factor research inputs qualify for a true point-in-time backtest.

This script deliberately does not promote the existing Naver current-view
fundamentals.  It maps them into the shared PIT evidence contract and records
why they remain research-only.  Once original-filing OpenDART snapshots are
available, the same contract can validate them before HML/CMA is rerun.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from toss_alpha.research.factor_pit import naver_current_view_candidate, validate_pit_contract

ROOT = Path(__file__).resolve().parents[1]
NAVER_CSV = ROOT / "reports" / "backtests" / "fundamental" / "naver_quarterly_fundamentals.csv"
PIT_PANEL_CSV = ROOT / "reports" / "backtests" / "pit_full_universe_2022-01-01_2026_ohlcv_panel.csv"
OUT = ROOT / "reports" / "validation" / "factor_pit_readiness_latest.json"


def build_readiness_report(naver: pd.DataFrame, pit_panel: pd.DataFrame, *, opendart_key_present: bool) -> dict:
    candidate = naver_current_view_candidate(naver)
    contract = validate_pit_contract(candidate, pit_panel)
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "factor_true_pit_readiness_gate",
        "current_input": {
            "source": "naver_current_view",
            "rows": int(len(naver)),
            "codes": int(naver["code"].astype(str).str.zfill(6).nunique()),
        },
        "opendart_collection": {
            "api_key_present": bool(opendart_key_present),
            "required_snapshot_semantics": "original_filing_or_revision_version_available_at_that_time",
            "single_account_latest_view_is_not_revision_safe": True,
        },
        "contract": contract.to_dict(),
        "promotion": {
            "status": "BLOCKED_TRUE_PIT_INPUTS" if not contract.eligible else "TRUE_PIT_INPUTS_READY",
            "hml_cma_live_promotion_allowed": False,
            "reason": (
                "true_pit_contract_not_satisfied"
                if not contract.eligible
                else "inputs_ready_but_strategy_requires_new_oos_backtest"
            ),
        },
        "next_requirements": [
            "collect original-filing or revision-versioned fundamentals with actual available_at dates",
            "retain estimate/actual and filing receipt identifiers",
            "intersect every rebalance snapshot with the historical PIT universe",
            "rerun HML/CMA with t-to-next-session execution and 31/50/75bp cost stress",
            "keep strategy research-only until independent OOS evidence passes",
        ],
    }


def main() -> int:
    if not NAVER_CSV.exists():
        raise FileNotFoundError(NAVER_CSV)
    if not PIT_PANEL_CSV.exists():
        raise FileNotFoundError(PIT_PANEL_CSV)

    naver = pd.read_csv(NAVER_CSV, dtype={"code": str})
    pit_panel = pd.read_csv(PIT_PANEL_CSV, dtype={"code": str})
    report = build_readiness_report(
        naver,
        pit_panel,
        opendart_key_present=bool(os.getenv("OPENDART_API_KEY")),
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    contract = report["contract"]
    print(f"factor_pit_status={contract['status']}")
    print(f"eligible={contract['eligible']}")
    print(f"reasons={','.join(contract['reasons'])}")
    print(f"opendart_api_key_present={report['opendart_collection']['api_key_present']}")
    print(f"output={OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
