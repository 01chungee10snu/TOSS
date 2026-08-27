from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
OPTIMIZER = SCRIPTS / "optimize_contextual_daily_strategy.py"
MONFRI = SCRIPTS / "analyze_contextual_mon_fri_cycle.py"
VALIDATOR = SCRIPTS / "validate_contextual_train_only_holdout.py"


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec.loader.exec_module(module)
    finally:
        try:
            sys.path.remove(str(SCRIPTS))
        except ValueError:
            pass
    return module


def train_perf(**overrides):
    base = {
        "sharpe": 1.0,
        "cagr_pct": 12.0,
        "max_drawdown_pct": -10.0,
        "total_return_pct": 30.0,
        "total_trades": 100,
    }
    base.update(overrides)
    return base


def row(**overrides):
    base = {
        "train_total_return_pct": 30.0,
        "train_sharpe": 1.0,
        "train_max_drawdown_pct": -10.0,
        "train_total_trades": 100,
        "test_total_return_pct": -50.0,
        "test_sharpe": -3.0,
        "test_max_drawdown_pct": -45.0,
        "test_total_trades": 80,
    }
    base.update(overrides)
    return base


def test_objective_is_invariant_to_holdout_results():
    m = load_script("contextual_optimizer_holdout_test", OPTIMIZER)
    train = train_perf()
    disastrous = {"sharpe": -10.0, "total_return_pct": -90.0, "max_drawdown_pct": -95.0, "total_trades": 0}
    amazing = {"sharpe": 10.0, "total_return_pct": 900.0, "max_drawdown_pct": -1.0, "total_trades": 10000}

    assert m.objective_score(train, disastrous) == m.objective_score(train, amazing)
    assert m.objective_score(train) == m.objective_score(train, disastrous)


def test_train_approval_never_depends_on_holdout():
    m = load_script("contextual_optimizer_train_gate_test", OPTIMIZER)
    bad_holdout = row()
    good_holdout = row(
        test_total_return_pct=80.0,
        test_sharpe=4.0,
        test_max_drawdown_pct=-2.0,
        test_total_trades=500,
    )

    assert m.train_approval_passed(bad_holdout) is True
    assert m.train_approval_passed(good_holdout) is True


def test_train_approval_rejects_weak_train_even_with_great_holdout():
    m = load_script("contextual_optimizer_train_weak_test", OPTIMIZER)
    weak_train = row(
        train_total_return_pct=-1.0,
        train_sharpe=-0.1,
        train_max_drawdown_pct=-5.0,
        train_total_trades=100,
        test_total_return_pct=100.0,
        test_sharpe=5.0,
        test_max_drawdown_pct=-1.0,
        test_total_trades=500,
    )
    assert m.train_approval_passed(weak_train) is False


def test_holdout_gate_is_diagnostic_only():
    m = load_script("contextual_optimizer_holdout_diag_test", OPTIMIZER)
    bad = row()
    good = row(
        test_total_return_pct=8.0,
        test_sharpe=0.8,
        test_max_drawdown_pct=-9.0,
        test_total_trades=m.MIN_TRADES_TEST,
    )
    assert m.holdout_diagnostic_passed(bad) is False
    assert m.holdout_diagnostic_passed(good) is True
    assert m.train_approval_passed(bad) == m.train_approval_passed(good)


def test_mon_fri_uses_same_train_only_contract():
    m = load_script("contextual_monfri_holdout_test", MONFRI)
    train = train_perf()
    terrible = {"sharpe": -20.0, "total_return_pct": -99.0, "max_drawdown_pct": -99.0, "total_trades": 0}
    excellent = {"sharpe": 20.0, "total_return_pct": 999.0, "max_drawdown_pct": 0.0, "total_trades": 9999}
    assert m.objective_score(train, terrible) == m.objective_score(train, excellent)
    assert m.train_approval_passed(row()) is True


def test_holdout_validator_rejects_negative_return_and_sharpe():
    m = load_script("contextual_holdout_validator_test", VALIDATOR)
    verdict = m.holdout_verdict(
        {
            "total_trades": 200,
            "total_return_pct": -2.0,
            "sharpe": -0.1,
            "max_drawdown_pct": -12.0,
        },
        min_trades=30,
    )
    assert verdict["passed"] is False
    assert "non_positive_holdout_return" in verdict["reasons"]
    assert "non_positive_holdout_sharpe" in verdict["reasons"]


def test_validator_grid_sizes_are_fixed_and_reproducible():
    m = load_script("contextual_holdout_grid_test", VALIDATOR)
    assert len(m.daily_grid()) == 144
    assert len(m.monfri_grid()) == 48
