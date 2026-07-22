from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from toss_alpha.execution.live_performance import (
    PerformanceThresholds,
    evaluate_live_performance,
    read_live_performance_gate,
)


def settlement(day: str, *, equity: float = 400_000, realized: float = 0, fills: int = 0, unmatched: float = 0) -> dict:
    return {
        "date": day,
        "account": {"total_equity": equity},
        "daily": {
            "realized_matched_fifo": realized,
            "fill_count": fills,
            "unmatched_sell_qty": unmatched,
        },
    }


def test_probation_continues_trading_with_small_initial_loss():
    result = evaluate_live_performance(
        [settlement("2026-07-20", realized=-893, fills=4)],
        policy_id="daily_multifactor_v1_practical400",
        deployed_since="2026-07-20",
    )

    assert result["status"] == "PROBATION_CONTINUE"
    assert result["block_new_buys"] is False
    assert result["preserve_sell_exits"] is True
    assert result["live_performance"]["cumulative_realized_matched_fifo_krw"] == -893
    assert result["research"]["review_required"] is True
    assert result["research"]["auto_replace_live_policy"] is False


def test_cumulative_loss_limit_blocks_only_new_buys():
    result = evaluate_live_performance(
        [
            settlement("2026-07-20", equity=400_000, realized=-7_000, fills=2),
            settlement("2026-07-21", equity=390_000, realized=-5_100, fills=2),
        ],
        policy_id="p",
        deployed_since="2026-07-20",
    )

    assert result["status"] == "BLOCK_NEW_BUYS"
    assert result["block_new_buys"] is True
    assert result["preserve_sell_exits"] is True
    assert "cumulative_realized_loss_limit" in result["reasons"]


def test_three_losing_fill_days_block_even_if_zero_fill_days_between():
    rows = [
        settlement("2026-07-20", realized=-100, fills=1),
        settlement("2026-07-21", realized=0, fills=0),
        settlement("2026-07-22", realized=-100, fills=1),
        settlement("2026-07-23", realized=-100, fills=1),
    ]
    result = evaluate_live_performance(rows, policy_id="p", deployed_since="2026-07-20")

    assert result["sample"]["consecutive_losing_fill_days"] == 3
    assert "consecutive_losing_fill_days_limit" in result["reasons"]
    assert result["block_new_buys"] is True


def test_unmatched_fifo_cost_basis_fails_closed():
    result = evaluate_live_performance(
        [
            settlement("2026-07-20", fills=1, unmatched=1),
            settlement("2026-07-21", fills=1, unmatched=2),
        ],
        policy_id="p",
        deployed_since="2026-07-20",
    )

    assert result["block_new_buys"] is True
    assert result["reasons"] == ["unmatched_fifo_sell_cost_basis"]
    assert result["live_performance"]["unmatched_sell_qty"] == 2
    assert result["live_performance"]["inherited_deployment_day_unmatched_sell_qty"] == 1


def test_deployment_day_unmatched_inventory_is_visible_but_does_not_freeze_new_policy():
    result = evaluate_live_performance(
        [settlement("2026-07-20", fills=1, unmatched=1)],
        policy_id="p",
        deployed_since="2026-07-20",
    )

    assert result["block_new_buys"] is False
    assert result["live_performance"]["unmatched_sell_qty"] == 0
    assert result["live_performance"]["inherited_deployment_day_unmatched_sell_qty"] == 1


def test_gate_reader_allows_missing_artifact_as_probation(tmp_path):
    result = read_live_performance_gate(tmp_path / "missing.json")
    assert result["status"] == "PROBATION_NO_ARTIFACT"
    assert result["block_new_buys"] is False


def test_gate_reader_blocks_corrupt_and_stale_artifacts(tmp_path):
    path = tmp_path / "gate.json"
    path.write_text("{bad", encoding="utf-8")
    assert read_live_performance_gate(path)["block_new_buys"] is True

    generated = datetime(2026, 7, 20, tzinfo=timezone.utc)
    path.write_text(json.dumps({"generated_at_utc": generated.isoformat(), "status": "PROBATION_CONTINUE", "block_new_buys": False}), encoding="utf-8")
    result = read_live_performance_gate(path, now=generated + timedelta(hours=97))
    assert result["status"] == "BLOCKED_STALE_ARTIFACT"
    assert result["block_new_buys"] is True


def test_custom_probation_threshold_can_reach_continue_state():
    result = evaluate_live_performance(
        [settlement("2026-07-20", fills=2)],
        policy_id="p",
        deployed_since="2026-07-20",
        thresholds=PerformanceThresholds(probation_settlement_days=1, probation_fills=2),
    )
    assert result["status"] == "CONTINUE_LIVE_OBSERVATION"
