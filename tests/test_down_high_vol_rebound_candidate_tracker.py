from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "track_down_high_vol_rebound_candidate.py"


def load_module():
    spec = importlib.util.spec_from_file_location("rebound_candidate_tracker", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def policy():
    return {
        "policy_id": "demo",
        "promotion_mode": "CANDIDATE_ONLY",
        "live_trading_enabled": False,
        "trigger": {
            "required_daily_regime": "down_high_vol",
            "required_intraday_verdict": "LONG_BUY",
            "required_market_regime": "risk_on",
            "required_market_override_confirmed": True,
            "required_evidence_status": "FRESH",
            "required_news_evidence_status": "FRESH",
            "min_market_day_return": 0.03,
            "earliest_kst": "11:00",
            "latest_kst": "15:20",
            "max_loop_report_age_seconds": 1200,
        },
        "research_exit": {"forward_mark_horizon_trading_days": 1},
    }


def report(observed="2026-07-24T03:00:00+00:00", market_return=0.031):
    return {
        "generated_at_utc": observed,
        "intraday": {
            "decision": {
                "decision_id": "d1",
                "generated_at_utc": observed,
                "daily_regime": "down_high_vol",
                "verdict": "LONG_BUY",
                "market_regime": "risk_on",
                "evidence_status": "FRESH",
                "news_evidence_status": "FRESH",
                "signal_conflict": False,
                "metrics": {
                    "market_override_confirmed": True,
                    "market_day_return": market_return,
                },
            }
        },
    }


def test_trigger_requires_fresh_point_in_time_three_percent_evidence():
    module = load_module()
    now = datetime(2026, 7, 24, 3, 5, tzinfo=timezone.utc)  # 12:05 KST

    passed = module.evaluate_trigger(report(), policy(), now=now)
    assert passed["triggered"] is True
    assert passed["blockers"] == []

    below = module.evaluate_trigger(report(market_return=0.0299), policy(), now=now)
    assert below["triggered"] is False
    assert "market_return_threshold" in below["blockers"]

    stale = module.evaluate_trigger(report("2026-07-24T02:00:00+00:00"), policy(), now=now)
    assert stale["triggered"] is False
    assert "loop_report_stale" in stale["blockers"]


def test_artifact_is_candidate_only_and_cannot_contain_orders():
    module = load_module()
    now = datetime(2026, 7, 24, 3, 5, tzinfo=timezone.utc)
    artifact = module.build_artifact(
        policy(),
        {"triggered": True},
        [{"symbol": "005930", "entry_mark": 100.0}],
        feature_date="2026-07-23",
        quote_errors={},
        now=now,
        status="CAPTURED",
    )
    assert artifact["promotion_mode"] == "CANDIDATE_ONLY"
    assert artifact["live_order_submission_prohibited"] is True
    assert artifact["live_order_submitted"] is False
    assert artifact["orders"] == []
    assert artifact["research_candidates"][0]["symbol"] == "005930"


def test_candidate_budget_respects_250k_per_symbol_cap():
    module = load_module()
    sized_policy = {
        "sizing": {
            "max_total_candidate_allocation_krw": 750_000,
            "max_notional_krw_per_position": 250_000,
        }
    }
    assert module.candidate_budget_krw(sized_policy, 1) == 250_000
    assert module.candidate_budget_krw(sized_policy, 3) == 250_000
    assert module.candidate_budget_krw(sized_policy, 4) == 187_500
    assert module.candidate_budget_krw(sized_policy, 0) == 0


def test_selection_uses_session_strictly_before_trigger_date():
    module = load_module()
    dates = pd.bdate_range("2026-05-20", periods=45)
    rows = []
    for symbol_index, symbol in enumerate(["111111", "222222", "333333"]):
        for i, date in enumerate(dates):
            # Oscillation avoids degenerate RSI while retaining sufficient volume.
            close = 10_000 + symbol_index * 1_000 + i * (10 + symbol_index * 2) + (80 if i % 2 else -60)
            rows.append({
                "Date": date.strftime("%Y-%m-%d"),
                "code": symbol,
                "name": f"N{symbol_index}",
                "Open": close - 10,
                "High": close + 30,
                "Low": close - 30,
                "Close": close,
                "Volume": 1_000_000 + (i % 3) * 50_000,
            })
    panel = pd.DataFrame(rows)
    base_policy = {
        "situations": {
            "up_low_vol": {
                "scoring_mode": "multi",
                "mode": "momentum",
                "momentum_col": "mom_5d",
                "min_dollar_volume": 500_000_000,
                "min_mom_5d": -0.10,
                "max_mom_5d": 0.15,
                "min_rsi": 1,
                "max_rsi": 99,
                "min_vol_ratio": 0.5,
                "max_vol_ratio": 3.0,
                "max_vol_20d": 0.08,
                "min_price": 2_000,
                "weights": {"momentum": 0.4, "low_vol": 0.25, "vol_norm": 0.15, "rsi_mid": 0.2},
            }
        }
    }
    trigger_date = dates[-1].strftime("%Y-%m-%d")
    feature_date, candidates = module.select_previous_session_candidates(
        panel,
        base_policy,
        decision_date_kst=trigger_date,
        top_n=3,
    )
    assert feature_date == dates[-2].strftime("%Y-%m-%d")
    assert candidates
    assert all(row["feature_date"] == feature_date for row in candidates)
