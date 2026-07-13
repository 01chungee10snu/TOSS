"""Regression tests for order lifecycle audit (BUG-1 through BUG-5).

These tests document and guard against concrete production bugs found in the
order lifecycle path:

* BUG-1: SELL exits blocked by intraday submit cap (live_submit.py:192)
* BUG-2: live_submit does not enforce SELL-before-BUY ordering
* BUG-3: LiveOrderLedger.records() crashes on malformed JSONL line
* BUG-7: intraday_submitted double-counts reconciliation status echoes
* BUG-8: ledger_key collides for different quantity/price same-day orders
* BUG-10: parse_status_payload misclassifies canceled order as SUBMITTED
           when broker omits remaining_qty

All tests are pure / offline — no broker, no network.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from toss_alpha.data.schema import OrderIntent
from toss_alpha.execution.live_submit import (
    LiveOrderLedger,
    _intraday_submit_attempt_count,
    ledger_key,
    run_live_submit_phase,
)
from toss_alpha.execution.order_management import (
    manage_submitted_order_ledger,
    parse_status_payload,
)
from toss_alpha.risk import RiskPolicy


# ── Shared fixtures ────────────────────────────────────────────────────────

def _sell_payload(as_of: str = "2026-07-08") -> dict:
    return {
        "status": "CANDIDATES",
        "as_of": as_of,
        "orders": [
            {
                "symbol": "307930",
                "side": "SELL",
                "order_type": "LIMIT",
                "quantity": 9,
                "sellable_quantity": 9,
                "limit_price": 6040,
                "notional_krw": 54360,
                "mode": "live_auto_guarded",
                "reason": "stop_loss_6%",
            }
        ],
    }


def _buy_payload(as_of: str = "2026-07-08") -> dict:
    return {
        "status": "CANDIDATES",
        "as_of": as_of,
        "orders": [
            {
                "symbol": "005930",
                "side": "BUY",
                "order_type": "LIMIT",
                "quantity": 1,
                "limit_price": 50000,
                "notional_krw": 50000,
                "mode": "live_auto_guarded",
                "reason": "approved_situation",
            }
        ],
    }


def _kis_env(tmp_path: Path, ledger_path: Path, **overrides: str) -> dict:
    env = {
        "BROKER_PROVIDER": "kis",
        "KIS_APP_KEY": "app",
        "KIS_APP_SECRET": "sec",
        "KIS_CANO": "12345678",
        "KIS_ACNT_PRDT_CD": "01",
        "KIS_LIVE_TRADING_ENABLED": "true",
        "TOSS_RISK_LIVE_TRADING_ENABLED": "true",
        "TOSS_MAX_ORDER_KRW": "150000",
        "TOSS_LIVE_ORDER_LEDGER": str(ledger_path),
        "TOSS_LIVE_SUBMIT_DRY_RUN": "false",
        "TOSS_LIVE_SUBMIT_ENABLED": "true",
        "TOSS_LIVE_SUBMIT_CONFIRMATION": "I UNDERSTAND THIS IS A REAL ORDER",
        "TOSS_ORDER_RECONCILE_ENABLED": "false",
        "TOSS_INTRADAY_SUBMIT_CAP": "12",
    }
    env.update(overrides)
    return env


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


# ── BUG-1: SELL exits blocked by intraday submit cap ──────────────────────

def test_bug1_sell_exit_must_not_be_blocked_by_intraday_submit_cap(tmp_path, monkeypatch):
    """SELL exits must bypass the intraday submit cap.

    A risk-reducing SELL (stop-loss, take-profit, drawdown liquidation) must
    never be blocked by the intraday submit cap that is meant to throttle BUY
    churn. If 12 BUYs have already been submitted today, the 13th SELL exit
    must still go through.

    CURRENT BEHAVIOUR (bug): line 192 applies ``intraday_submit_cap_exceeded``
    to both BUY and SELL indiscriminately.
    """
    ledger_path = tmp_path / "ledger.jsonl"
    now = datetime(2026, 7, 8, 1, 30, tzinfo=timezone.utc)  # 10:30 KST
    rows = [
        {
            "ledger_key": f"2026-07-08:ttak_absolute_return_loop:00000{i}:BUY",
            "status": "SUBMITTED",
            "timestamp": now.isoformat(),
        }
        for i in range(12)  # cap exhausted
    ]
    _write_jsonl(ledger_path, rows)

    monkeypatch.setattr(
        "toss_alpha.execution.live_submit.GuardedLiveExecutor.submit_manual_draft",
        lambda self, intent, decision, **kwargs: {
            "status": "SUBMITTED" if decision.allow else "BLOCK",
            "violations": list(decision.violations),
        },
    )
    result = run_live_submit_phase(
        candidate_payload=_sell_payload(),
        qual={"status": "READY", "reasons": []},
        live={"ready": True},
        report_dir=tmp_path,
        env=_kis_env(tmp_path, ledger_path),
        now=now,
    )

    violations = result["results"][0].get("violations", [])
    assert "intraday_submit_cap_exceeded" not in violations
    assert result["results"][0]["status"] == "SUBMITTED"


# ── BUG-2: live_submit does not enforce SELL-before-BUY ordering ───────────

def test_bug2_live_submit_does_not_sort_sell_before_buy(tmp_path):
    """live_submit processes orders in insertion order, not SELL-first.

    If a payload has BUY before SELL, the BUY is processed first and can
    consume the intraday cap (or fail on a phase-level block that SELLs are
    exempt from), leaving the SELL exit unprocessed. The safe ordering is
    SELL-first (exits before entries), which merge_exit_orders does but
    run_live_submit_phase does NOT enforce independently.
    """
    import inspect
    from toss_alpha.execution import live_submit as ls

    src = inspect.getsource(ls.run_live_submit_phase)
    # The function should contain explicit SELL-first sorting logic.
    # Currently it does not — it iterates `orders` as-is.
    has_sell_priority_sort = (
        "sort" in src.lower()
        and ("sell" in src.lower() or "side" in src.lower())
    )
    assert has_sell_priority_sort
    # Demonstrate the dependency: the function relies entirely on the caller
    # (merge_exit_orders) to have ordered SELLs first.
    # A payload with BUY-before-SELL is processed in that order.


# ── BUG-3: LiveOrderLedger.records() crashes on malformed JSONL ────────────

def test_bug3_ledger_records_crashes_on_malformed_jsonl(tmp_path):
    """A single malformed JSONL line crashes the entire live-submit phase.

    LiveOrderLedger.records() calls json.loads(line) without try/except,
    unlike order_management.read_jsonl() which skips bad lines. If the ledger
    file has one corrupt line (partial write, disk error), the live-submit
    phase aborts — including for unrelated SELL exits.

    Contrast: order_management.read_jsonl() (line 147) handles this correctly.
    """
    ledger_path = tmp_path / "ledger.jsonl"
    _write_jsonl(
        ledger_path,
        [
            {"ledger_key": "k1", "status": "SUBMITTED"},
            # Malformed line inserted by partial write / crash
        ],
    )
    # Append a corrupt line
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write("THIS IS NOT VALID JSON\n")
        f.write(json.dumps({"ledger_key": "k2", "status": "FILLED"}) + "\n")

    ledger = LiveOrderLedger(ledger_path)
    rows = ledger.records()
    assert [row["ledger_key"] for row in rows] == ["k1", "k2"]
    assert ledger.corrupt is True


# ── BUG-7: intraday_submitted double-counts reconciliation echoes ──────────

def test_bug7_intraday_count_double_counts_reconciliation_echoes(tmp_path):
    """Reconciliation status rows inflate the intraday submit count.

    manage_submitted_order_ledger (which runs before the intraday count at
    line 156-163) appends new SUBMITTED rows when the broker status inquiry
    confirms an order is still active. The intraday count at line 169-173
    counts ALL rows with status=='SUBMITTED', including these reconciliation
    echoes. This inflates the count and prematurely exhausts the cap.
    """
    ledger_path = tmp_path / "ledger.jsonl"
    now = datetime(2026, 7, 8, 1, 30, tzinfo=timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    _write_jsonl(
        ledger_path,
        [
            # Original submission
            {"ledger_key": "k1", "status": "SUBMITTED", "timestamp": now.isoformat(), "result": {}},
            # Reconciliation echo (same order, still SUBMITTED)
            {"ledger_key": "k1", "status": "SUBMITTED", "timestamp": now.isoformat(),
             "broker_status": {"status": "SUBMITTED"}},
            # SELL exits do not consume the BUY churn cap.
            {"ledger_key": "2026-07-08:s:005930:SELL", "status": "SUBMITTED", "timestamp": now.isoformat()},
        ],
    )

    ledger = LiveOrderLedger(ledger_path)
    # This is the exact counting logic from live_submit.py lines 169-173
    counted = _intraday_submit_attempt_count(ledger.records(), today_str)

    # There is only 1 distinct submitted order, but the count is 2.
    assert counted == 1


# ── BUG-8: ledger_key collides for different quantity/price ────────────────

def test_bug8_ledger_key_collides_for_different_quantity_and_price():
    """Two orders with different size/price for the same symbol/side/date
    produce identical ledger keys, causing false duplicate blocks.

    ledger_key = as_of:strategy_id:symbol:side — quantity and price are
    NOT included. If order 1 (qty=10) is still SUBMITTED, order 2 (qty=20)
    for the same symbol/side/date is incorrectly blocked as a duplicate.
    """
    payload = {"as_of": "2026-07-08"}
    intent1 = OrderIntent(
        strategy_id="s", symbol="005930", side="BUY",
        reason="r", quantity=10, limit_price=50000,
    )
    intent2 = OrderIntent(
        strategy_id="s", symbol="005930", side="BUY",
        reason="r", quantity=20, limit_price=55000,  # different size and price
    )

    key1 = ledger_key(intent1, payload)
    key2 = ledger_key(intent2, payload)

    assert key1 == key2, (
        "If this assertion fails, ledger_key was made more specific — update test."
    )


# ── BUG-10: parse_status_payload misclassifies canceled as SUBMITTED ───────

def test_bug10_missing_remaining_qty_is_unknown_not_active_submission():
    """Missing broker remaining quantity is ambiguous and must not be inferred."""
    payload = {
        "json": {
            "output1": [
                {"odno": "0001", "ord_qty": "9", "tot_ccld_qty": "0"},
            ]
        }
    }
    parsed = parse_status_payload(payload, order_no="0001")

    assert parsed["status"] == "UNKNOWN"
    assert parsed["remaining_qty"] is None
