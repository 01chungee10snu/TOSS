#!/usr/bin/env python3
"""Integration test: inverse sleeve re-entry cooldown breaks buy-sell-buy loop."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from toss_alpha.execution.position_exit import build_position_exit_orders
from toss_alpha.execution.inverse_sleeve import maybe_apply_inverse_sleeve
from toss_alpha.data.schema import PositionSnapshot


def test_inverse_cooldown_blocks_reentry_after_regime_recovery(tmp_path):
    """After inverse_regime_recovery exit, re-buying within cooldown is blocked."""
    tracker_path = tmp_path / "live_position_tracker.json"

    # Phase 1: inverse position held, market turns risk_on → regime recovery exit
    positions = [
        PositionSnapshot(
            symbol="114800",
            quantity=47,
            sellable_quantity=47,
            avg_price=1009,
            market_value=47 * 1005,
            source="kis",
        ),
    ]

    # Write a tracker with pre-existing first_seen so grace period doesn't block
    tracker_data = {
        "114800": {
            "first_seen_date": (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat(),
            "peak_price": 1020,
            "avg_price": 1009,
            "quantity": 47,
        }
    }
    tracker_path.write_text(json.dumps(tracker_data))

    orders, audit = build_position_exit_orders(
        positions,
        env={
            "TOSS_POSITION_RISK_OFF_EXIT": "true",
            "TOSS_INVERSE_RECOVERY_MIN_HOURS": "2",
        },
        market_regime="risk_on",
        report_dir=tmp_path,
    )

    assert audit["sell_order_count"] == 1
    assert "inverse_regime_recovery" in orders[0]["reason"]

    # Verify tracker now has last_regime_recovery_date
    tracker = json.loads(tracker_path.read_text())
    assert "last_regime_recovery_date" in tracker.get("114800", {})
    print(f"✓ Phase 1: regime recovery exit recorded: {tracker['114800']['last_regime_recovery_date']}")

    # Phase 2: ttak loop tries to buy inverse again — cooldown should block
    candidate_payload = {
        "status": "CANDIDATES",
        "policy_id": "test",
        "situation": "down_high_vol",
        "intraday_decision": {"verdict": "INVERSE_BUY"},
        "as_of": "2026-07-30",
        "orders": [],
    }

    # Use tmp_path as out_dir — simulate CANDIDATE_DIR
    # The tracker is in tmp_path itself for this test
    transformed, sleeve_audit = maybe_apply_inverse_sleeve(
        candidate_payload,
        out_dir=tmp_path,
        env={
            "TOSS_INVERSE_SLEEVE_ENABLED": "true",
            "TOSS_INVERSE_REENTRY_COOLDOWN_HOURS": "24",
        },
    )

    assert transformed["status"] == "NO_TRADE"
    assert "cooldown" in transformed["reason"]
    print(f"✓ Phase 2: re-entry blocked: {transformed['reason']}")


def test_fresh_position_grace_blocks_immediate_regime_risk_off(tmp_path):
    """Position bought <4h ago should not be liquidated by regime_risk_off."""
    tracker_path = tmp_path / "live_position_tracker.json"
    tracker_data = {
        "307930": {
            "first_seen_date": (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),
            "peak_price": 6050,
            "avg_price": 6000,
            "quantity": 9,
        }
    }
    tracker_path.write_text(json.dumps(tracker_data))

    positions = [
        PositionSnapshot(
            symbol="307930",
            quantity=9,
            sellable_quantity=9,
            avg_price=6000,
            market_value=9 * 6050,
            source="kis",
        ),
    ]

    orders, audit = build_position_exit_orders(
        positions,
        env={
            "TOSS_POSITION_RISK_OFF_EXIT": "true",
            "TOSS_FRESH_POSITION_MIN_HOLD_HOURS": "4",
        },
        market_regime="risk_off",
        report_dir=tmp_path,
    )

    assert audit["sell_order_count"] == 0
    assert audit["reviews"][0]["action"] == "HOLD"
    print("✓ Fresh position (30min old) protected from regime_risk_off by 4h grace")


def test_old_position_still_exits_on_regime_risk_off(tmp_path):
    """Position held >4h should still be liquidated by regime_risk_off."""
    tracker_path = tmp_path / "live_position_tracker.json"
    tracker_data = {
        "307930": {
            "first_seen_date": (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat(),
            "peak_price": 6050,
            "avg_price": 6000,
            "quantity": 9,
        }
    }
    tracker_path.write_text(json.dumps(tracker_data))

    positions = [
        PositionSnapshot(
            symbol="307930",
            quantity=9,
            sellable_quantity=9,
            avg_price=6000,
            market_value=9 * 6050,
            source="kis",
        ),
    ]

    orders, audit = build_position_exit_orders(
        positions,
        env={
            "TOSS_POSITION_RISK_OFF_EXIT": "true",
            "TOSS_FRESH_POSITION_MIN_HOLD_HOURS": "4",
        },
        market_regime="risk_off",
        report_dir=tmp_path,
    )

    assert audit["sell_order_count"] == 1
    assert "regime_risk_off" in orders[0]["reason"]
    print("✓ Old position (10h old) correctly liquidated by regime_risk_off")


def test_cooldown_metadata_preserved_after_position_cleanup(tmp_path):
    """When inverse position is fully exited, cooldown metadata survives cleanup."""
    tracker_path = tmp_path / "live_position_tracker.json"
    tracker_data = {
        "114800": {
            "first_seen_date": (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat(),
            "peak_price": 1020,
            "avg_price": 1009,
            "quantity": 47,
        }
    }
    tracker_path.write_text(json.dumps(tracker_data))

    # Position gets exited via regime recovery
    positions = [
        PositionSnapshot(
            symbol="114800",
            quantity=47,
            sellable_quantity=47,
            avg_price=1009,
            market_value=47 * 1005,
            source="kis",
        ),
    ]

    orders, audit = build_position_exit_orders(
        positions,
        env={
            "TOSS_POSITION_RISK_OFF_EXIT": "true",
            "TOSS_INVERSE_RECOVERY_MIN_HOURS": "2",
        },
        market_regime="risk_on",
        report_dir=tmp_path,
    )

    assert audit["sell_order_count"] == 1

    # After cleanup, no positions are held — but 114800 cooldown should survive
    tracker = json.loads(tracker_path.read_text())
    assert "114800" in tracker
    assert "last_regime_recovery_date" in tracker["114800"]
    print(f"✓ Cooldown metadata preserved after cleanup: {tracker['114800']}")


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        test_inverse_cooldown_blocks_reentry_after_regime_recovery(td)
        test_fresh_position_grace_blocks_immediate_regime_risk_off(td)
        test_old_position_still_exits_on_regime_risk_off(td)
        test_cooldown_metadata_preserved_after_position_cleanup(td)
    print("\n✅ All oscillation-loop tests passed")
