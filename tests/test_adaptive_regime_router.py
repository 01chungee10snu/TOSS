from __future__ import annotations

from datetime import datetime, timezone

from toss_alpha.execution.adaptive_regime_router import build_shadow_plan

NOW = datetime(2026, 8, 12, 3, 30, tzinfo=timezone.utc)


def _base(**overrides):
    payload = {
        "now": NOW,
        "intraday_decision": {
            "generated_at_utc": NOW.isoformat(),
            "evidence_status": "FRESH",
            "news_evidence_status": "FRESH",
            "verdict": "LONG_BUY",
            "daily_regime": "up_high_vol",
            "metrics": {"market_day_return": 0.03},
        },
        "forward_report": {
            "predict_date": "2026-08-11",
            "macro_regime": {"status": "risk_on"},
            "top10": [
                {"code": "005930", "name": "삼성전자", "ml_score": 0.4, "close": 100000, "volume": 1000000},
            ],
        },
        "sector_screen": {
            "generated_at_kst": "2026-08-12T12:10:00+09:00",
            "drill_down": [
                {
                    "sector": "반도체",
                    "sector_return": 0.04,
                    "stocks": [
                        {"code": "005930", "day_return": 0.03, "last": 102000, "volume": 1000000},
                        {"code": "000660", "day_return": 0.09, "last": 200000, "volume": 500000},
                    ],
                }
            ],
        },
        "equity_guard": {"block_new_buys": False, "status": "READY"},
        "performance_gate": {"block_new_buys": False, "status": "PROBATION_CONTINUE"},
        "max_notional_krw": 100000,
    }
    payload.update(overrides)
    return payload


def test_up_high_vol_routes_to_confirmed_sector_leader():
    plan = build_shadow_plan(**_base())
    assert plan["status"] == "SHADOW_CANDIDATES"
    assert plan["strategy"] == "sector_momentum"
    assert [o["symbol"] for o in plan["orders"]] == ["005930"]
    assert plan["orders"][0]["quantity"] == 0  # shadow plans never create executable quantity
    assert plan["orders"][0]["shadow_notional_krw"] <= 100000


def test_risk_off_defaults_to_cash_until_inverse_is_validated():
    decision = _base()["intraday_decision"] | {
        "verdict": "INVERSE_BUY",
        "daily_regime": "down_high_vol",
        "market_regime": "risk_off",
    }
    plan = build_shadow_plan(**_base(intraday_decision=decision))
    assert plan["status"] == "NO_TRADE"
    assert plan["strategy"] == "cash"
    assert plan["orders"] == []
    assert "inverse_strategy_not_validated" in plan["reasons"]


def test_stale_intraday_evidence_fails_closed():
    decision = _base()["intraday_decision"] | {"generated_at_utc": "2026-08-12T03:20:00+00:00"}
    plan = build_shadow_plan(**_base(intraday_decision=decision))
    assert plan["status"] == "NO_TRADE"
    assert "stale_intraday_decision" in plan["reasons"]


def test_equity_or_performance_gate_blocks_all_new_buys():
    plan = build_shadow_plan(**_base(equity_guard={"block_new_buys": True, "status": "BLOCKED"}))
    assert plan["status"] == "NO_TRADE"
    assert plan["orders"] == []
    assert "equity_guard_block" in plan["reasons"]


def test_forward_candidate_with_zero_volume_is_rejected():
    report = _base()["forward_report"] | {
        "top10": [{"code": "000880", "name": "한화", "ml_score": 0.5, "close": 83800, "volume": 0}]
    }
    decision = _base()["intraday_decision"] | {"daily_regime": "up_low_vol"}
    plan = build_shadow_plan(**_base(intraday_decision=decision, forward_report=report, sector_screen={}))
    assert plan["status"] == "NO_TRADE"
    assert "no_quality_long_candidates" in plan["reasons"]


def test_shadow_contract_never_emits_live_execution_fields():
    plan = build_shadow_plan(**_base())
    assert plan["execution_stage"] == "shadow_only"
    assert plan["live_order_submitted"] is False
    assert all(o.get("quantity") == 0 for o in plan["orders"])
    assert all("ledger_key" not in o for o in plan["orders"])
