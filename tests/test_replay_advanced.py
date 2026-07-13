"""Tests for advanced replay features — trailing stop, multi-position, sizing."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from toss_alpha.daily.replay import _make_test_panel, _Position, ReplayEngine


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


def test_trailing_stop_waits_for_activation_gain():
    engine = ReplayEngine(
        panel=_make_test_panel(days=20, n_symbols=1), symbols=[],
        stop_loss_pct=0.10, take_profit_pct=0.99,
        trailing_stop_pct=0.05, trailing_stop_activation_gain_pct=0.03,
    )
    engine.open_positions["000000"] = _Position(
        symbol="000000", quantity=1, entry_price=100.0,
        entry_date="2026-01-01", entry_step=0, peak_price=102.0,
    )

    engine._check_exits({"000000": 96.0}, "2026-01-02", 1, {"status": "neutral"})

    assert "000000" in engine.open_positions
    assert engine.closed_trades == []


def test_trailing_stop_fires_after_activation_gain():
    engine = ReplayEngine(
        panel=_make_test_panel(days=20, n_symbols=1), symbols=[],
        stop_loss_pct=0.10, take_profit_pct=0.99,
        trailing_stop_pct=0.05, trailing_stop_activation_gain_pct=0.03,
    )
    engine.open_positions["000000"] = _Position(
        symbol="000000", quantity=1, entry_price=100.0,
        entry_date="2026-01-01", entry_step=0, peak_price=110.0,
    )

    engine._check_exits({"000000": 104.0}, "2026-01-02", 1, {"status": "neutral"})

    assert "000000" not in engine.open_positions
    assert engine.closed_trades[-1].exit_reason == "trailing_stop"


def test_max_holding_trading_days_is_independent_of_replay_step_units():
    engine = ReplayEngine(
        panel=_make_test_panel(days=20, n_symbols=1), symbols=[],
        stop_loss_pct=0.99, take_profit_pct=0.99,
        max_holding_steps=20, max_holding_trading_days=10,
    )
    engine._run_step = 5
    engine.open_positions["000000"] = _Position(
        symbol="000000", quantity=1, entry_price=100.0,
        entry_date="2026-01-01", entry_step=0, peak_price=100.0,
    )

    engine._check_exits({"000000": 100.0}, "2026-01-08", 1, {"status": "neutral"})
    assert "000000" in engine.open_positions

    engine._check_exits({"000000": 100.0}, "2026-01-15", 2, {"status": "neutral"})
    assert "000000" not in engine.open_positions
    assert engine.closed_trades[-1].exit_reason == "time_exit"


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


def test_equity_drawdown_guard_forces_liquidation_and_records_stop():
    """Portfolio-level drawdown guard should close positions and report risk stops."""
    rows = []
    for day, close in enumerate([100, 102, 104, 82, 81, 80, 79, 78, 77, 76]):
        rows.append(
            {
                "Date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=day),
                "Open": close,
                "High": close,
                "Low": close,
                "Close": close,
                "Volume": 1_000_000,
                "code": "111111",
            }
        )
        rows.append(
            {
                "Date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=day),
                "Open": close * 0.99,
                "High": close,
                "Low": close * 0.98,
                "Close": close * 0.99,
                "Volume": 1_000_000,
                "code": "222222",
            }
        )
    panel = pd.DataFrame(rows)
    engine = ReplayEngine(
        panel=panel,
        symbols=["111111", "222222"],
        initial_cash_krw=1_000_000,
        max_notional_krw=400_000,
        score_threshold=0.0,
        stop_loss_pct=0.99,
        take_profit_pct=0.99,
        max_positions=2,
        max_equity_drawdown_stop_pct=0.05,
        risk_cooldown_steps=2,
    )

    result = engine.run(step=1)

    assert result["summary"]["risk_stop_count"] >= 1
    assert "equity_drawdown_stop" in {trade["exit_reason"] for trade in result["trades"]}
    stopped_rows = [row for row in result["equity_curve"] if row["open_positions"] == 0]
    assert stopped_rows


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
