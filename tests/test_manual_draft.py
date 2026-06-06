import inspect

import toss_alpha.agents.execution_draft as execution_draft
from toss_alpha.agents.execution_draft import build_manual_draft
from toss_alpha.data.schema import OrderIntent, RiskDecision


def _intent():
    return OrderIntent(
        strategy_id="s1",
        symbol="005930",
        side="BUY",
        notional_krw=100000,
        reason="research candidate",
    )


def test_draft_includes_manual_draft_only_mode():
    draft = build_manual_draft(_intent(), RiskDecision.allowed(), rationale="momentum", evidence=["backtest"])
    assert draft["mode"] == "manual_draft_only"
    assert draft["intent"]["mode"] == "manual_draft_only"


def test_blocked_risk_decision_blocks_draft():
    draft = build_manual_draft(_intent(), RiskDecision.blocked(["missing_data"]), rationale="x", evidence=[])
    assert draft["status"] == "BLOCK"
    assert "missing_data" in draft["violations"]


def test_draft_text_contains_required_korean_guardrails():
    draft = build_manual_draft(_intent(), RiskDecision.allowed(), rationale="momentum", evidence=["backtest"])
    assert "실주문 아님" in draft["markdown"]
    assert "수동 확인 필요" in draft["markdown"]


def test_module_has_no_execution_callables():
    forbidden = {"execute", "place_order", "submit_order", "buy", "sell"}
    callables = {name for name, value in inspect.getmembers(execution_draft, callable)}
    assert forbidden.isdisjoint(callables)
