from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "strategy_meta_allocator.py"


def load_module():
    spec = importlib.util.spec_from_file_location("strategy_meta_allocator_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_forward_drawdown_evidence_is_percent_to_decimal_and_strategy_scoped(tmp_path, monkeypatch):
    m = load_module()
    paper = tmp_path / "paper.json"
    paper.write_text(
        json.dumps({"forward_shadow": {"metrics": {"max_drawdown_pct": -1.25}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "FORWARD_PAPER_PATH", paper)
    monkeypatch.setattr(m, "ROOT", tmp_path)

    values, meta = m._current_drawdown_evidence(["forward"])

    assert values == {"forward": -0.0125}
    assert meta["status"] == "AVAILABLE"
    assert meta["strategy_id"] == "forward"
    assert meta["max_drawdown_pct"] == -1.25


def test_build_report_keeps_paper_strategy_out_of_live_allocation_and_protects_forward(tmp_path, monkeypatch):
    m = load_module()
    paper = tmp_path / "paper.json"
    paper.write_text(
        json.dumps({"forward_shadow": {"metrics": {"max_drawdown_pct": -2.0}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "FORWARD_PAPER_PATH", paper)
    monkeypatch.setattr(m, "ROOT", tmp_path)

    n = 500
    idx = pd.bdate_range("2024-01-02", periods=n)
    base = np.sin(np.arange(n) / 10.0) * 0.01 + 0.0002
    returns = {
        "rank1": pd.Series(base, index=idx),
        "forward": pd.Series(base * 0.98, index=idx),
    }
    tournament = {
        "decision": "NO_NEW_LIVE_PROMOTION",
        "leaderboard": [
            {
                "strategy_id": "rank1",
                "rank": 1,
                "status": "PAPER_CANDIDATE",
                "evidence_grade": "B",
                "notes": [],
            },
            {
                "strategy_id": "forward",
                "rank": 2,
                "status": "PAPER_CANDIDATE",
                "evidence_grade": "B",
                "notes": ["current forward-paper target; gate_passed=False"],
            },
        ],
    }
    coverage = {sid: {"observations": n} for sid in returns}

    report = m.build_report(tournament, returns, coverage)

    assert report["order_submission"] is False
    assert report["live_allocation"]["weights"] == {}
    assert report["live_allocation"]["cash_weight"] == 1.0
    assert report["research_shadow_allocation"]["selected_after_correlation"] == ["forward"]
    assert report["research_shadow_allocation"]["correlation_pruning"]["removed"]["rank1"]["duplicate_of"] == "forward"
    assert report["current_drawdown_evidence"]["strategy_id"] == "forward"
    assert report["governance"]["historical_backtest_end_drawdown_not_used_as_current_risk_state"] is True


def test_strict_factor_curve_is_loaded_for_correlation_but_research_only_remains_unsized(tmp_path, monkeypatch):
    m = load_module()
    validation = tmp_path / "reports" / "validation"
    validation.mkdir(parents=True)
    curve = pd.DataFrame(
        {
            "date": pd.bdate_range("2022-06-30", periods=300),
            "equity": 100_000_000 * np.cumprod(1.0 + np.sin(np.arange(300) / 11.0) * 0.003 + 0.0003),
        }
    )
    curve.to_csv(validation / "hml_cma_true_pit_profitability_proxy_75bp_curve.csv", index=False)
    tournament = {
        "decision": "NO_NEW_LIVE_PROMOTION",
        "leaderboard": [
            {
                "strategy_id": "profitability_proxy",
                "rank": 10,
                "family": "true_pit_domestic_factor",
                "status": "RESEARCH_ONLY",
                "evidence_grade": "C",
                "source": "reports/validation/hml_cma_true_pit_latest.json",
                "notes": [],
            }
        ],
    }
    monkeypatch.setattr(m, "ROOT", tmp_path)

    series, coverage = m.build_strict_factor_return_series(tournament, validation_dir=validation)

    assert "profitability_proxy" in series
    assert len(series["profitability_proxy"]) == 299
    assert coverage["profitability_proxy"]["status"] == "AVAILABLE"
    metrics = {sid: m.risk_metrics(ret) for sid, ret in series.items()}
    report = m.build_report(tournament, series, coverage)
    assert metrics["profitability_proxy"].observations == 299
    assert report["live_allocation"]["weights"] == {}
    assert report["research_shadow_allocation"]["weights"] == {}
    assert "status_below_paper_candidate" in report["research_shadow_allocation"]["blocked"]["profitability_proxy"]


def test_factor_independence_requires_both_pearson_and_downside_below_point_eight():
    m = load_module()
    leaderboard = [
        {"strategy_id": "forward", "family": "executable_etf"},
        {"strategy_id": "profitability_proxy", "family": "true_pit_domestic_factor"},
    ]
    correlations = {
        "pairs": {
            "forward|profitability_proxy": {
                "observations": 500,
                "pearson": 0.42,
                "downside": 0.81,
            }
        }
    }
    blocked = m._factor_independence_vs_forward(leaderboard, correlations, ["forward"])
    assert blocked["profitability_proxy"]["passed_independence_check"] is False

    correlations["pairs"]["forward|profitability_proxy"]["downside"] = 0.55
    passed = m._factor_independence_vs_forward(leaderboard, correlations, ["forward"])
    assert passed["profitability_proxy"]["passed_independence_check"] is True
    assert passed["profitability_proxy"]["threshold"] == 0.80
