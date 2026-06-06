from toss_alpha.data.schema import BacktestResult, ResearchGoal, RiskDecision, SignalResult
from toss_alpha.reports.markdown_report import render_research_report


def test_report_includes_required_risk_disclaimers():
    report = render_research_report(
        goal=ResearchGoal(
            goal_id="g1",
            mode="backtest_only",
            symbols=["005930"],
            start="2024-01-01",
            end="2024-12-31",
            strategy_name="momentum",
        ),
        signals=[SignalResult(name="momentum", score=0.5, rationale="up")],
        backtest=BacktestResult(strategy_id="s1", status="PASS", total_return=0.1),
        risk_decision=RiskDecision.blocked(["missing_data"]),
        manual_draft={"markdown": "초안"},
        data_as_of="2026-06-06T00:00:00Z",
    )
    assert "투자 조언 아님" in report
    assert "손실 가능" in report
    assert "BLOCK" in report
    assert "매수하세요" not in report
    assert "매도하세요" not in report
