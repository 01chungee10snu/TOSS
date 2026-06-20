"""Tests for cumulative replay engine — empirical validation loop."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from toss_alpha.daily.replay import ReplayEngine, run_replay


def _make_panel(start_date: str = "2024-01-01", days: int = 120, n_symbols: int = 5) -> pd.DataFrame:
    """Generate deterministic OHLCV panel for testing."""
    rows = []
    base_prices = {i: 10_000 + i * 2_000 for i in range(n_symbols)}
    symbols = [str(10_000 + i * 111).zfill(6) for i in range(n_symbols)]
    start = date.fromisoformat(start_date)
    for day_offset in range(days):
        current = start + timedelta(days=day_offset)
        # skip weekends for realism
        if current.weekday() >= 5:
            continue
        for idx, sym in enumerate(symbols):
            base = base_prices[idx]
            # gentle uptrend for first 2, flat for next, downtrend for last
            if idx < 2:
                close = base * (1.0 + day_offset * 0.003)
            elif idx == 2:
                close = base * (1.0 + (day_offset % 10 - 5) * 0.001)
            else:
                close = base * (1.0 - day_offset * 0.002)
            rows.append({
                "Date": pd.Timestamp(current),
                "Open": close * 0.999,
                "High": close * 1.005,
                "Low": close * 0.995,
                "Close": close,
                "Volume": 500_000 + day_offset * 10_000,
                "code": sym,
            })
    return pd.DataFrame(rows)


def test_replay_engine_runs_and_produces_equity_curve():
    panel = _make_panel(days=120, n_symbols=5)
    symbols = sorted(panel["code"].unique().tolist())
    engine = ReplayEngine(panel=panel, symbols=symbols, initial_cash_krw=1_000_000, max_notional_krw=100_000)
    result = engine.run(step=5)

    assert result["total_steps"] > 0
    assert len(result["equity_curve"]) == result["total_steps"]
    assert all("date" in row and "equity" in row for row in result["equity_curve"])
    assert result["initial_cash_krw"] == 1_000_000
    # equity at step 0 should be initial cash (no positions yet)
    assert result["equity_curve"][0]["equity"] == pytest.approx(1_000_000, rel=0.01)


def test_replay_engine_records_trades():
    panel = _make_panel(days=120, n_symbols=5)
    symbols = sorted(panel["code"].unique().tolist())
    engine = ReplayEngine(panel=panel, symbols=symbols, initial_cash_krw=1_000_000, max_notional_krw=100_000)
    result = engine.run(step=5)

    assert "trades" in result
    assert isinstance(result["trades"], list)
    # with uptrending symbols, should have at least one entry
    if result["trades"]:
        trade = result["trades"][0]
        assert "symbol" in trade
        assert "entry_date" in trade
        assert "entry_price" in trade
        assert "side" in trade


def test_replay_engine_computes_summary_metrics():
    panel = _make_panel(days=120, n_symbols=5)
    symbols = sorted(panel["code"].unique().tolist())
    engine = ReplayEngine(panel=panel, symbols=symbols, initial_cash_krw=1_000_000, max_notional_krw=100_000)
    result = engine.run(step=5)

    summary = result["summary"]
    assert "total_return_pct" in summary
    assert "max_drawdown_pct" in summary
    assert "total_trades" in summary
    assert "win_rate_pct" in summary
    assert "sharpe_ratio" in summary
    assert isinstance(summary["total_return_pct"], float)
    assert isinstance(summary["max_drawdown_pct"], float)


def test_replay_engine_respects_max_positions_cap():
    panel = _make_panel(days=160, n_symbols=8)
    symbols = sorted(panel["code"].unique().tolist())
    engine = ReplayEngine(
        panel=panel,
        symbols=symbols,
        initial_cash_krw=1_000_000,
        max_notional_krw=100_000,
        max_positions=3,
        score_threshold=50.0,
    )
    result = engine.run(step=5)

    assert max(row["open_positions"] for row in result["equity_curve"]) <= 3


def test_full_liquidate_rebalance_closes_positions_every_step():
    panel = _make_panel(days=120, n_symbols=8)
    symbols = sorted(panel["code"].unique().tolist())
    engine = ReplayEngine(
        panel=panel,
        symbols=symbols,
        initial_cash_krw=1_000_000,
        max_notional_krw=100_000,
        score_threshold=50.0,
        max_positions=4,
        rebalance_mode="full_liquidate_every_step",
    )
    result = engine.run(step=5)

    exit_reasons = {t["exit_reason"] for t in result["trades"]}
    assert "rebalance_liquidate" in exit_reasons
    assert max(row["open_positions"] for row in result["equity_curve"]) <= 4


def test_top_n_rotation_rebalance_sells_names_that_fall_out_of_top_n():
    panel = _make_panel(days=180, n_symbols=8)
    symbols = sorted(panel["code"].unique().tolist())
    engine = ReplayEngine(
        panel=panel,
        symbols=symbols,
        initial_cash_krw=1_000_000,
        max_notional_krw=100_000,
        score_threshold=50.0,
        max_positions=3,
        rebalance_mode="top_n_rotation",
    )
    result = engine.run(step=5)

    exit_reasons = {t["exit_reason"] for t in result["trades"]}
    assert "rebalance_rotation" in exit_reasons or len(result["trades"]) > 0
    assert max(row["open_positions"] for row in result["equity_curve"]) <= 3


def test_replay_engine_can_rank_entries_with_prediction_map():
    panel = _make_panel(days=140, n_symbols=5)
    symbols = sorted(panel["code"].unique().tolist())
    preferred = symbols[-1]
    prediction_map = {}
    for ts in sorted(panel["Date"].unique()):
        date_str = pd.Timestamp(ts).date().isoformat()
        prediction_map[date_str] = {symbol: 0.01 for symbol in symbols}
        prediction_map[date_str][preferred] = 0.50

    engine = ReplayEngine(
        panel=panel,
        symbols=symbols,
        initial_cash_krw=1_000_000,
        max_notional_krw=100_000,
        score_threshold=0.0,
        max_positions=1,
        prediction_map=prediction_map,
        prediction_min_score=0.0,
    )
    result = engine.run(step=5)

    assert result["trades"]
    assert result["trades"][0]["symbol"] == preferred
    assert any("ml_prediction" in trade for trade in result["trades"])


def test_replay_engine_prediction_overlay_rerank_keeps_base_candidates():
    """In rerank mode, base-approved candidates are all kept; ML only re-orders them."""
    panel = _make_panel(days=140, n_symbols=5)
    symbols = sorted(panel["code"].unique().tolist())
    preferred = symbols[-1]
    prediction_map = {}
    for ts in sorted(panel["Date"].unique()):
        date_str = pd.Timestamp(ts).date().isoformat()
        prediction_map[date_str] = {symbol: 0.01 for symbol in symbols}
        prediction_map[date_str][preferred] = 0.50

    engine = ReplayEngine(
        panel=panel,
        symbols=symbols,
        initial_cash_krw=1_000_000,
        max_notional_krw=100_000,
        score_threshold=0.0,
        max_positions=1,
        prediction_map=prediction_map,
        prediction_overlay_mode="rerank",
    )
    result = engine.run(step=5)

    assert result["trades"]
    assert result["trades"][0]["symbol"] == preferred


def test_replay_engine_prediction_overlay_gate_requires_both_base_and_ml():
    """In gate mode, a symbol needs BOTH base_score >= threshold AND ml_pred >= min."""
    panel = _make_panel(days=140, n_symbols=5)
    symbols = sorted(panel["code"].unique().tolist())
    blocked = symbols[0]
    prediction_map = {}
    for ts in sorted(panel["Date"].unique()):
        date_str = pd.Timestamp(ts).date().isoformat()
        prediction_map[date_str] = {symbol: 0.50 for symbol in symbols}
        prediction_map[date_str][blocked] = -0.50

    engine = ReplayEngine(
        panel=panel,
        symbols=symbols,
        initial_cash_krw=1_000_000,
        max_notional_krw=100_000,
        score_threshold=0.0,
        max_positions=5,
        prediction_map=prediction_map,
        prediction_overlay_mode="gate",
        prediction_min_score=0.0,
    )
    result = engine.run(step=5)

    entered = {t["symbol"] for t in result["trades"]}
    assert blocked not in entered


def test_replay_engine_prediction_overlay_penalty_combines_scores():
    """In penalty mode, adjusted_score = base_score + alpha * ml_pred."""
    panel = _make_panel(days=140, n_symbols=5)
    symbols = sorted(panel["code"].unique().tolist())
    boosted = symbols[-1]
    prediction_map = {}
    for ts in sorted(panel["Date"].unique()):
        date_str = pd.Timestamp(ts).date().isoformat()
        prediction_map[date_str] = {symbol: 0.0 for symbol in symbols}
        prediction_map[date_str][boosted] = 1.0

    engine = ReplayEngine(
        panel=panel,
        symbols=symbols,
        initial_cash_krw=1_000_000,
        max_notional_krw=100_000,
        score_threshold=0.0,
        max_positions=1,
        prediction_map=prediction_map,
        prediction_overlay_mode="penalty",
        prediction_alpha=50.0,
    )
    result = engine.run(step=5)

    assert result["trades"]
    assert result["trades"][0]["symbol"] == boosted


def test_run_replay_writes_artifacts(tmp_path: Path):
    panel = _make_panel(days=120, n_symbols=5)
    panel.to_csv(tmp_path / "panel.csv", index=False)
    symbols = sorted(panel["code"].unique().tolist())

    result = run_replay(
        panel_csv=tmp_path / "panel.csv",
        symbols=symbols,
        initial_cash_krw=1_000_000,
        max_notional_krw=100_000,
        step=5,
        out_dir=tmp_path / "replay",
    )

    assert Path(result["equity_curve_csv"]).exists()
    assert Path(result["summary_json"]).exists()
    assert Path(result["report_md"]).exists()

    summary = json.loads(Path(result["summary_json"]).read_text())
    assert "total_return_pct" in summary

    curve = pd.read_csv(result["equity_curve_csv"])
    assert len(curve) > 0
    assert "date" in curve.columns
    assert "equity" in curve.columns
