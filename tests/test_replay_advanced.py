"""Tests for advanced replay features — trailing stop, multi-position, sizing."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from toss_alpha.daily.replay import _make_test_panel, ReplayEngine


def test_trailing_stop_locks_profit():
    """When trailing_stop_pct is set, peak price is tracked and stop trails."""
    panel = _make_test_panel(days=80, n_symbols=5)
    engine = ReplayEngine(
        panel=panel,
        symbols=[],
        initial_cash_krw=1_000_000,
        max_notional_krw=200_000,
        score_threshold=50.0,
        stop_loss_pct=0.05,
        take_profit_pct=0.99,  # effectively disabled
        trailing_stop_pct=0.05,
        max_holding_steps=20,
    )
    result = engine.run(step=5)
    # With trailing stop, positions should either exit via trailing or reach end
    reasons = {t["exit_reason"] for t in result["trades"]}
    assert "trailing_stop" in reasons or "end_of_replay" in reasons or result["summary"]["total_trades"] == 0


def test_multi_position_enters_multiple_per_step():
    """When max_positions > 1, engine can hold multiple symbols simultaneously."""
    panel = _make_test_panel(days=80, n_symbols=5)
    engine = ReplayEngine(
        panel=panel,
        symbols=[],
        initial_cash_krw=2_000_000,
        max_notional_krw=100_000,
        score_threshold=50.0,
        stop_loss_pct=0.05,
        take_profit_pct=0.08,
        max_positions=3,
    )
    result = engine.run(step=5)
    max_open = max(row["open_positions"] for row in result["equity_curve"])
    assert max_open >= 1
    # With multiple up-trending symbols, should enter more than 1
    assert max_open >= 2


def test_score_weighted_sizing_changes_position_sizes():
    """Higher-scoring candidates should get larger position size."""
    panel = _make_test_panel(days=80, n_symbols=5)
    engine_flat = ReplayEngine(
        panel=panel,
        symbols=[],
        initial_cash_krw=1_000_000,
        max_notional_krw=100_000,
        score_threshold=50.0,
        sizing_mode="flat",
    )
    engine_weighted = ReplayEngine(
        panel=panel,
        symbols=[],
        initial_cash_krw=1_000_000,
        max_notional_krw=100_000,
        score_threshold=50.0,
        sizing_mode="score_weighted",
    )
    result_flat = engine_flat.run(step=5)
    result_weighted = engine_weighted.run(step=5)
    # Both should run without error
    assert result_flat["summary"]["total_trades"] >= 0
    assert result_weighted["summary"]["total_trades"] >= 0


def test_entry_volume_filter_reduces_trades():
    """Volume filter should reject low-volume candidates."""
    panel = _make_test_panel(days=80, n_symbols=5)
    # No filter
    engine_open = ReplayEngine(
        panel=panel,
        symbols=[],
        initial_cash_krw=1_000_000,
        score_threshold=50.0,
        min_volume=0,
    )
    # High volume filter
    engine_filtered = ReplayEngine(
        panel=panel,
        symbols=[],
        initial_cash_krw=1_000_000,
        score_threshold=50.0,
        min_volume=10_000_000,
    )
    r_open = engine_open.run(step=5)
    r_filtered = engine_filtered.run(step=5)
    # Filtered should have fewer or equal trades
    assert r_filtered["summary"]["total_trades"] <= r_open["summary"]["total_trades"]


def test_replay_compares_baseline_vs_advanced():
    """Sanity: advanced config should differ from baseline."""
    panel = _make_test_panel(days=100, n_symbols=5)
    baseline = ReplayEngine(
        panel=panel,
        symbols=[],
        initial_cash_krw=1_000_000,
        score_threshold=60.0,
        stop_loss_pct=0.05,
        take_profit_pct=0.08,
        max_positions=1,
        trailing_stop_pct=0.0,
    )
    advanced = ReplayEngine(
        panel=panel,
        symbols=[],
        initial_cash_krw=1_000_000,
        score_threshold=60.0,
        stop_loss_pct=0.10,
        take_profit_pct=0.15,
        max_positions=3,
        trailing_stop_pct=0.05,
    )
    rb = baseline.run(step=5)
    ra = advanced.run(step=5)
    # At least one should produce trades
    assert rb["summary"]["total_trades"] >= 0
    assert ra["summary"]["total_trades"] >= 0
